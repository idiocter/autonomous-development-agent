"""Shared Anthropic client wrapper and two call patterns used by every agent:

- `call_structured`: single-shot, forces the model to respond via exactly one
  tool call matching a JSON schema -- used where we want a reliable typed
  result (e.g. the Planner's list of plan steps), not prose.
- `run_tool_loop`: a generic agentic loop that executes any tool_use blocks
  against provided Python handlers and feeds results back until the model
  stops calling tools -- used where the agent needs to read/edit files
  (Coding, Debugging).
"""

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

import anthropic

from src.agents.usage import is_over_budget, record_usage
from src.config import settings
from src.tools.filesystem_tools import list_dir, read_file, str_replace, write_file

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
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
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_content}],
        tools=[
            {
                "name": output_tool_name,
                "description": "Submit the final structured result.",
                "input_schema": output_schema,
            }
        ],
        tool_choice={"type": "tool", "name": output_tool_name},
    )
    record_usage(model, response.usage.input_tokens, response.usage.output_tokens)
    for block in response.content:
        if block.type == "tool_use" and block.name == output_tool_name:
            return block.input
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
        {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
    ]
    handlers = {t.name: t.handler for t in tools}

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

    for _ in range(max_turns):
        if is_over_budget():
            # Independent of max_turns -- a tool-calling loop that's blown
            # its cost budget should stop mid-loop, not burn the rest of its
            # turn allowance before the caller notices.
            break

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tool_defs,
        )
        record_usage(model, response.usage.input_tokens, response.usage.output_tokens)
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = []
        for block in tool_uses:
            handler = handlers.get(block.name)
            try:
                result = handler(**block.input) if handler else f"unknown tool: {block.name}"
                content = "ok" if result is None else str(result)
                is_error = False
            except Exception as exc:  # noqa: BLE001 -- fed back to the model, not raised
                content = f"error: {exc}"
                is_error = True
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return messages


def extract_text(messages: list[dict[str, Any]]) -> str:
    return "".join(
        block.text
        for message in messages
        if message["role"] == "assistant"
        for block in message["content"]
        if getattr(block, "type", None) == "text"
    )


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
