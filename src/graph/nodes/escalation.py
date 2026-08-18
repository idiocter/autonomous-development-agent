"""Reached when a loop-safety guard trips (see routing.route_after_test).
Posts a transparent comment on the issue explaining what was tried and tags
it for a human -- never opens a broken PR.
"""

from src.graph.state import AgentState
from src.tools.github_tools import add_label, build_escalation_comment, comment_on_issue, get_client, get_repo


def human_escalation_node(state: AgentState) -> dict:
    if not state["repo_full_name"]:
        # Local-only run (Phase 1 toy-repo demo) -- nothing to post to.
        return {"status": "needs_human"}

    history_summary = "\n".join(
        f"- attempt {i + 1}: `{r['command']}` -> "
        f"{'PASS' if r['passed'] else r['failure_signature']}"
        for i, r in enumerate(state["test_history"])
    )
    client = get_client()
    gh_repo = get_repo(client, state["repo_full_name"])
    comment_on_issue(
        gh_repo,
        state["issue_number"],
        build_escalation_comment(
            debug_analysis=state["debug_analysis"], test_history_summary=history_summary
        ),
    )
    add_label(gh_repo, state["issue_number"], "agent:needs-human")

    return {"status": "needs_human"}
