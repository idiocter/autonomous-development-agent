from src.agents.base import extract_text, filesystem_tool_specs, run_tool_loop
from src.config import settings
from src.graph.state import AgentState
from src.tools.rag_tools import rag_retrieve

_SYSTEM = """You are the Coding agent in an autonomous software development pipeline.

You have tools to read, write, and edit files in the repo working directory.
Implement exactly the plan given to you, then stop -- do not make unrelated
changes. Prefer str_replace for small, targeted edits over rewriting whole
files. When you are done, briefly summarize what you changed and why."""


def call_coder(state: AgentState) -> str:
    """Runs the coding agent's tool loop against the working tree. Returns
    the agent's closing summary; the actual changes are picked up by the
    caller inspecting the working tree / git diff, not returned here.
    """
    repo_root = state["repo_local_path"]
    repo_url = state["repo_full_name"] or repo_root
    plan_text = "\n".join(
        f"- [{s['action']}] {s['file']}: {s['description']}" for s in state["plan_steps"]
    )
    debug_note = (
        f"\n\nA previous attempt failed testing. Debugging analysis to address:\n{state['debug_analysis']}"
        if state["debug_analysis"]
        else ""
    )

    # Scoped narrower than the Planner's broad retrieval -- query built from
    # the plan itself and the specific target files, not the raw issue text.
    target_files = ", ".join(sorted({s["file"] for s in state["plan_steps"]}))
    rag_query = f"{plan_text}\ntarget files: {target_files}"
    context_chunks = rag_retrieve(repo_root, rag_query, k=6, repo_url=repo_url)
    context_block = "\n\n".join(
        f"--- {c['file_path']}:{c['start_line']}-{c['end_line']} ---\n{c['content']}"
        for c in context_chunks
    )

    user_content = (
        f"Issue: {state['issue_title']}\n\n"
        f"Plan to implement:\n{plan_text}{debug_note}\n\n"
        f"Relevant existing code:\n{context_block or '(none retrieved)'}\n\n"
        "Use the tools available to make the necessary changes now."
    )

    messages = run_tool_loop(
        model=settings.coder_model,
        system=_SYSTEM,
        user_content=user_content,
        tools=filesystem_tool_specs(repo_root),
    )
    return extract_text(messages) or "changes applied"
