from src.agents.base import call_structured
from src.config import settings
from src.graph.state import AgentState, PlanStep

_SYSTEM = """You are the Planner agent in an autonomous software development pipeline.

Given a GitHub issue and relevant repo context, produce a concrete, minimal
list of file-level changes needed to resolve the issue. Do not write code --
just plan which files to touch and what to do to each. Keep the plan as small
as possible while fully addressing the issue."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "plan_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "path relative to repo root"},
                    "action": {"type": "string", "enum": ["create", "edit", "delete"]},
                    "description": {"type": "string"},
                },
                "required": ["file", "action", "description"],
            },
        }
    },
    "required": ["plan_steps"],
}


def call_planner(state: AgentState) -> list[PlanStep]:
    context_blocks = "\n\n".join(
        f"--- {c['file_path']}:{c['start_line']}-{c['end_line']} ---\n{c['content']}"
        for c in state["relevant_context"]
    )
    user_content = (
        f"Issue title: {state['issue_title']}\n\n"
        f"Issue body:\n{state['issue_body']}\n\n"
        f"Relevant repo context:\n{context_blocks or '(none retrieved)'}"
    )
    result = call_structured(
        model=settings.planner_model,
        system=_SYSTEM,
        user_content=user_content,
        output_schema=_SCHEMA,
        output_tool_name="submit_plan",
    )
    return result["plan_steps"]
