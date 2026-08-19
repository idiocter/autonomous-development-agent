"""Covers the OpenAI-specific request/response shapes in agents/base.py --
the parts that differ structurally from the Anthropic implementation on
`main` and would otherwise only fail against a live API:
  - tool arguments arrive as a JSON string, not a dict
  - usage is prompt_tokens/completion_tokens (wrong names => the cost
    budget guard silently records zero)
  - tool replies are role="tool" messages keyed by tool_call_id
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.agents import base, usage


def _fake_response(*, tool_calls=None, content=None, prompt_tokens=100, completion_tokens=50):
    message = SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def _fake_tool_call(call_id, name, arguments: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(base, "get_client", lambda: client)
    usage._current_budget.set(None)
    return client


def test_call_structured_parses_json_string_arguments(mock_client):
    mock_client.chat.completions.create.return_value = _fake_response(
        tool_calls=[_fake_tool_call("c1", "submit_result", {"steps": ["a", "b"]})]
    )

    result = base.call_structured(
        model="gpt-4o",
        system="sys",
        user_content="user",
        output_schema={"type": "object"},
        output_tool_name="submit_result",
    )

    assert result == {"steps": ["a", "b"]}  # decoded, not left as a JSON string


def test_call_structured_sends_system_as_a_message(mock_client):
    mock_client.chat.completions.create.return_value = _fake_response(
        tool_calls=[_fake_tool_call("c1", "submit_result", {})]
    )

    base.call_structured(
        model="gpt-4o", system="SYSTEM_PROMPT", user_content="user", output_schema={"type": "object"}
    )

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": "SYSTEM_PROMPT"}
    assert "system" not in kwargs  # not an Anthropic-style top-level param


def test_call_structured_raises_when_no_tool_call_returned(mock_client):
    mock_client.chat.completions.create.return_value = _fake_response(tool_calls=None, content="prose")

    with pytest.raises(RuntimeError, match="did not return structured output"):
        base.call_structured(
            model="gpt-4o", system="s", user_content="u", output_schema={"type": "object"}
        )


def test_usage_is_recorded_from_openai_token_field_names(mock_client):
    usage.start_job_budget("job-openai", budget_usd=10.0)
    mock_client.chat.completions.create.return_value = _fake_response(
        tool_calls=[_fake_tool_call("c1", "submit_result", {})],
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
    )

    base.call_structured(
        model="gpt-4o", system="s", user_content="u", output_schema={"type": "object"}
    )

    budget = usage.get_job_budget()
    assert budget.total_input_tokens == 1_000_000
    assert budget.total_output_tokens == 1_000_000
    assert budget.total_cost_usd == 2.50 + 10.0  # would be 0.0 if the field names were wrong
    usage._current_budget.set(None)


def test_run_tool_loop_executes_handler_and_replies_with_tool_role(mock_client):
    calls = {}

    def handler(path):
        calls["path"] = path
        return "file contents"

    spec = base.ToolSpec(
        name="read_file",
        description="read",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        handler=handler,
    )

    mock_client.chat.completions.create.side_effect = [
        _fake_response(tool_calls=[_fake_tool_call("call_1", "read_file", {"path": "a.py"})]),
        _fake_response(tool_calls=None, content="done, I read it"),
    ]

    messages = base.run_tool_loop(model="gpt-4o", system="s", user_content="u", tools=[spec])

    assert calls["path"] == "a.py"  # JSON-string args were decoded before **kwargs
    tool_replies = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_replies) == 1
    assert tool_replies[0]["tool_call_id"] == "call_1"
    assert tool_replies[0]["content"] == "file contents"
    assert base.extract_text(messages) == "done, I read it"


def test_run_tool_loop_feeds_handler_errors_back_instead_of_raising(mock_client):
    def boom(**_):
        raise ValueError("nope")

    spec = base.ToolSpec(
        name="read_file", description="read", input_schema={"type": "object"}, handler=boom
    )
    mock_client.chat.completions.create.side_effect = [
        _fake_response(tool_calls=[_fake_tool_call("call_1", "read_file", {})]),
        _fake_response(tool_calls=None, content="recovered"),
    ]

    messages = base.run_tool_loop(model="gpt-4o", system="s", user_content="u", tools=[spec])

    tool_replies = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert "error: nope" in tool_replies[0]["content"]


def test_run_tool_loop_stops_when_over_budget(mock_client):
    usage.start_job_budget("job-broke", budget_usd=0.0001)
    usage.record_usage("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)

    base.run_tool_loop(model="gpt-4o", system="s", user_content="u", tools=[])

    mock_client.chat.completions.create.assert_not_called()
    usage._current_budget.set(None)
