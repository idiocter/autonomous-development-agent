"""Shared OpenAI client wrapper and two call patterns used by every agent:

- `call_structured`: single-shot, forces the model to respond via exactly one
  tool call matching a JSON schema -- used where we want a reliable typed
  result (e.g. the Planner's list of plan steps), not prose.
- `run_tool_loop`: a generic agentic loop that executes any tool calls
  against provided Python handlers and feeds results back until the model
  stops calling tools -- used where the agent needs to read/edit files
  (Coding, Debugging).

Differences from the Anthropic implementation on `main`, since they bite:
  - the system prompt is a message with role="system", not a separate param
  - `tool_calls[].function.arguments` is a JSON *string* needing json.loads,
    not an already-decoded dict
  - tool results go back as separate role="tool" messages keyed by
    tool_call_id, rather than tool_result blocks inside one user message
  - usage fields are prompt_tokens/completion_tokens, not input/output_tokens
    (get this wrong and the cost-budget loop guard silently records zero)
"""

import json
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

from openai import OpenAI

from src.agents.usage import is_over_budget, record_usage
from src.config import settings
from src.tools.filesystem_tools import list_dir, read_file, str_replace, write_file

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set in .env")
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]


def call_structured(
    *,
    model: str,
    system: str,
    user_content: str,
    output_schema: dict[str, Any],
    output_tool_name: str = "submit_result",
    max_tokens: int = 4096,
) -> dict[str, Any]:
    client = get_client()
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": output_tool_name,
                    "description": "Submit the final structured result.",
                    "parameters": output_schema,
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": output_tool_name}},
    )
    record_usage(model, response.usage.prompt_tokens, response.usage.completion_tokens)

    for call in response.choices[0].message.tool_calls or []:
        if call.function.name == output_tool_name:
            return json.loads(call.function.arguments)
    raise RuntimeError("model did not return structured output")


def run_tool_loop(
    *,
    model: str,
    system: str,
    user_content: str,
    tools: list[ToolSpec],
    max_turns: int = 8,
    max_tokens: int = 4096,
) -> list[dict[str, Any]]:
    client = get_client()
    tool_defs = [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
        }
        for t in tools
    ]
    handlers = {t.name: t.handler for t in tools}

    messages: list[Any] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    for _ in range(max_turns):
        if is_over_budget():
            # Independent of max_turns -- a tool-calling loop that's blown
            # its cost budget should stop mid-loop, not burn the rest of its
            # turn allowance before the caller notices.
            break

        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            tools=tool_defs,
        )
        record_usage(model, response.usage.prompt_tokens, response.usage.completion_tokens)

        message = response.choices[0].message
        # The assistant message carrying tool_calls must be appended before
        # the corresponding role="tool" replies, or the API rejects them as
        # orphaned tool_call_ids.
        messages.append(message)

        tool_calls = message.tool_calls or []
        if not tool_calls:
            break

        for call in tool_calls:
            handler = handlers.get(call.function.name)
            try:
                args = json.loads(call.function.arguments or "{}")
                result = handler(**args) if handler else f"unknown tool: {call.function.name}"
                content = "ok" if result is None else str(result)
            except Exception as exc:  # noqa: BLE001 -- fed back to the model, not raised
                content = f"error: {exc}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

    return messages


def extract_text(messages: list[Any]) -> str:
    """Concatenates assistant prose across the loop. The list is a mix of
    plain dicts (the seeded system/user messages, and role="tool" replies)
    and OpenAI message objects, so this handles both shapes rather than
    assuming one -- and skips tool-call-only turns, whose content is None.
    """
    parts: list[str] = []
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
        if role != "assistant":
            continue
        content = (
            message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        )
        if content:
            parts.append(content)
    return "".join(parts)


def filesystem_tool_specs(repo_root: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="read_file",
            description="Read a file's contents. Path is relative to the repo root.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=partial(read_file, repo_root),
        ),
        ToolSpec(
            name="write_file",
            description="Overwrite (or create) a file with new content. Path is relative to the repo root.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            handler=partial(write_file, repo_root),
        ),
        ToolSpec(
            name="str_replace",
            description=(
                "Replace a single, unique occurrence of old_str with new_str in a file. "
                "Fails if old_str is missing or not unique -- re-read the file and retry "
                "with more context rather than guessing."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
            handler=partial(str_replace, repo_root),
        ),
        ToolSpec(
            name="list_dir",
            description="List entries in a directory. Path is relative to the repo root.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
            },
            handler=partial(list_dir, repo_root),
        ),
    ]
