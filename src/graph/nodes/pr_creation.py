"""Phase 2: real GitHub PR creation. Only reached when tests pass (see
routing.route_after_test) -- this node's authority stops at opening a PR;
merging is a human action, never automatic.
"""

import git
import structlog

from src.github_integration.auth import get_auth_provider
from src.graph.state import AgentState
from src.tools.github_tools import (
    build_pr_body,
    comment_on_issue,
    commit_all,
    create_pr,
    get_client,
    get_repo,
    push_branch,
)

logger = structlog.get_logger(__name__)


def pr_node(state: AgentState) -> dict:
    if not state["repo_full_name"]:
        # Local-only run (Phase 1 toy-repo demo) -- nothing to push.
        return {"status": "done", "pr_url": None}

    token = get_auth_provider().get_token()
    local_repo = git.Repo(state["repo_local_path"])

    work_branch = state["work_branch"] or f"agent/issue-{state['issue_number']}-{state['job_id'][:8]}"
    had_changes = commit_all(local_repo, state["commit_message"] or f"Fix issue #{state['issue_number']}")

    if not had_changes:
        return {
            "status": "failed",
            "error_log": state["error_log"] + ["coding agent made no changes to commit"],
        }

    push_branch(local_repo, work_branch, token, state["repo_full_name"])

    client = get_client()
    gh_repo = get_repo(client, state["repo_full_name"])

    plan_summary = "\n".join(
        f"- [{s['action']}] {s['file']}: {s['description']}" for s in state["plan_steps"]
    )
    files_changed = sorted({s["file"] for s in state["plan_steps"]})
    test_result = state["test_result"]

    pr = create_pr(
        gh_repo,
        title=f"Fix #{state['issue_number']}: {state['issue_title']}",
        body=build_pr_body(
            issue_number=state["issue_number"],
            plan_summary=plan_summary,
            files_changed=files_changed,
            test_command=test_result["command"] if test_result else "",
            test_passed=test_result["passed"] if test_result else False,
        ),
        head=work_branch,
        base=state["base_branch"],
    )
    # Courtesy backlink only -- the PR already exists by this point, so a
    # failure here (commonly a token without Issues:write) must not sink an
    # otherwise successful job and lose the PR URL. Observed for real on the
    # first live run: the PR was created, then the whole job raised on this
    # line and reported failure.
    try:
        comment_on_issue(gh_repo, state["issue_number"], f"Opened {pr.html_url}")
    except Exception as exc:  # noqa: BLE001 -- non-fatal, recorded not raised
        logger.warning(
            "could not comment PR link back on issue",
            issue=state["issue_number"],
            pr_url=pr.html_url,
            error=str(exc),
        )

    return {"status": "done", "pr_url": pr.html_url, "work_branch": work_branch}
