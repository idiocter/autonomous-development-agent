import structlog

from src.agents.base import call_structured
from src.config import settings
from src.graph.state import AgentState, PlanStep
from src.security.prompt_guard import (
    UNTRUSTED_CONTENT_RULE,
    scan_for_injection,
    wrap_untrusted,
)

logger = structlog.get_logger(__name__)

_SYSTEM = """You are the Planner agent in an autonomous software development pipeline.

Given a GitHub issue and relevant repo context, produce a concrete, minimal
list of file-level changes needed to resolve the issue. Do not write code --
just plan which files to touch and what to do to each. Keep the plan as small
as possible while fully addressing the issue.

Do NOT plan any change to a test file unless the issue explicitly asks for
tests to be added or changed. Tests define the expected behaviour: the fix
belongs in the source code that the tests exercise. A failing test means the
source is wrong, not the test. Never plan to edit a test so that it matches
current behaviour.

""" + UNTRUSTED_CONTENT_RULE

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

    # Issue text is attacker-controlled on any public repo, and retrieved repo
    # content can carry planted instructions too -- fence both.
    issue_text = f"Title: {state['issue_title']}\n\n{state['issue_body']}"
    findings = {
        "issue": scan_for_injection(issue_text),
        "repo_context": scan_for_injection(context_blocks),
    }
    if any(findings.values()):
        logger.warning(
            "possible prompt injection in untrusted input",
            job_id=state.get("job_id"),
            findings={k: v for k, v in findings.items() if v},
        )

    user_content = (
        f"Issue to resolve:\n{wrap_untrusted(issue_text, 'issue')}\n\n"
        f"Relevant repo context:\n"
        f"{wrap_untrusted(context_blocks, 'repo_context') if context_blocks else '(none retrieved)'}"
    )
    result = call_structured(
        model=settings.planner_model,
        system=_SYSTEM,
        user_content=user_content,
        output_schema=_SCHEMA,
        output_tool_name="submit_plan",
    )
    return result["plan_steps"]
