import re

from src.agents.base import extract_text, filesystem_tool_specs, run_tool_loop
from src.config import settings
from src.graph.state import AgentState
from src.tools.rag_tools import rag_retrieve

_SYSTEM = """You are the Debugging agent in an autonomous software development pipeline.

Tests are failing. Investigate using the file tools available, form a
root-cause hypothesis, and apply a concrete fix. Prefer the smallest change
that fixes the root cause over a broad rewrite. When done, briefly summarize
the root cause and the fix you made."""


def _extract_traceback_query(output: str) -> str:
    """Pulls file:line references and the exception's last line out of a
    traceback -- a much more targeted RAG query than embedding the raw,
    often-huge stdout/stderr blob.
    """
    file_refs = re.findall(r'File "([^"]+)", line (\d+)', output)
    last_line = output.strip().splitlines()[-1] if output.strip() else ""
    refs_text = " ".join(f"{f}:{n}" for f, n in file_refs[-3:])
    return f"{last_line} {refs_text}".strip()


def call_debugger(state: AgentState) -> str:
    repo_root = state["repo_local_path"]
    repo_url = state["repo_full_name"] or repo_root
    last_result = state["test_result"]
    assert last_result is not None, "call_debugger requires a failing test_result in state"

    history_lines = [
        f"attempt {i + 1}: {r['failure_signature']}" for i, r in enumerate(state["test_history"])
    ]

    rag_query = _extract_traceback_query(last_result["stderr"] or last_result["stdout"])
    context_chunks = rag_retrieve(repo_root, rag_query, k=6, repo_url=repo_url) if rag_query else []
    context_block = "\n\n".join(
        f"--- {c['file_path']}:{c['start_line']}-{c['end_line']} ---\n{c['content']}"
        for c in context_chunks
    )

    user_content = (
        f"Issue: {state['issue_title']}\n\n"
        f"Failing command: {last_result['command']}\n"
        f"Exit code: {last_result['exit_code']}\n\n"
        f"stdout:\n{last_result['stdout']}\n\n"
        f"stderr:\n{last_result['stderr']}\n\n"
        f"Prior attempts in this job:\n{chr(10).join(history_lines) or '(first attempt)'}\n\n"
        f"Relevant existing code:\n{context_block or '(none retrieved)'}\n\n"
        "Diagnose the root cause and apply a fix using the available tools."
    )

    messages = run_tool_loop(
        model=settings.debugger_model,
        system=_SYSTEM,
        user_content=user_content,
        tools=filesystem_tool_specs(repo_root),
    )
    return extract_text(messages) or "fix applied"
