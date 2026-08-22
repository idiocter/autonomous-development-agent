"""Reached when a loop-safety guard trips (see routing.give_up_reason).

Hands the run over rather than just abandoning it: commits whatever the agent
managed, pushes it, and opens a *draft* PR so the human inherits real code
instead of a apology. The node's original rule was "never opens a broken PR",
which was right about not presenting red work as mergeable -- draft says "not
ready to merge", which is exactly what this is.

Everything here is mechanical. It makes no model calls, which matters: one of
the three give-up conditions is budget exhaustion, so this node can be entered
with the budget already spent, and call_structured has no budget check.

Every external call is wrapped, and so is the node as a whole. An exception
escaping here doesn't just lose the comment -- _instrumented re-raises,
run_job catches only TimeoutError, so update_job_status never runs and the job
row is left at "testing" with a null completed_at, permanently mid-flight in
the API. The node must always return a terminal status.
"""

from collections.abc import Callable
from typing import Any, TypeVar

import git
import structlog
from github.GithubException import GithubException

from src.agents.usage import get_job_budget
from src.config import settings
from src.github_integration.auth import get_auth_provider
from src.graph.routing import give_up_reason
from src.graph.state import AgentState
from src.tools.github_tools import (
    add_label,
    build_commit_message,
    build_escalation_comment,
    changed_files,
    comment_on_issue,
    commit_all,
    create_pr,
    diff_stat,
    get_client,
    get_repo,
    push_branch,
)

logger = structlog.get_logger(__name__)

T = TypeVar("T")


def _best_effort(fn: Callable[[], T], message: str, **log_ctx: Any) -> T | None:
    """Run a step, log and continue if it fails.

    The handoff degrades one piece at a time: a failed push still leaves a
    committed branch and a comment that says where it is, which beats losing
    the whole handover because one API call was denied.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- handing off must not itself fail
        logger.warning(message, error=str(exc), **log_ctx)
        return None


def _attempts_table(state: AgentState) -> str:
    """The test history as a table -- the repeat pattern that triggers a
    give-up is visible at a glance in a way a bullet list doesn't manage."""
    history = state["test_history"]
    if not history:
        return ""
    rows = [
        f"| {i + 1} | `{r['command']}` | "
        f"{'✅ passed' if r['passed'] else '❌ `' + str(r['failure_signature']) + '`'} |"
        for i, r in enumerate(history)
    ]
    return "\n".join(["| # | Command | Result |", "|---|---|---|", *rows])


def human_escalation_node(state: AgentState) -> dict:
    result: dict[str, Any] = {"status": "needs_human"}
    try:
        return _hand_off(state, result)
    except Exception as exc:  # noqa: BLE001 -- see module docstring
        logger.warning("escalation handoff failed", job_id=state.get("job_id"), error=str(exc))
        return result


def _hand_off(state: AgentState, result: dict[str, Any]) -> dict[str, Any]:
    reason = give_up_reason(state)
    budget = get_job_budget()
    last = state["test_result"]

    if not state["repo_full_name"]:
        # Local-only run -- the work is already in the caller's workspace and
        # there's nowhere to post to.
        logger.info("giving up on local run", job_id=state.get("job_id"), reason=reason)
        return result

    work_branch = (
        state["work_branch"] or f"agent/issue-{state['issue_number']}-{state['job_id'][:8]}"
    )
    result["work_branch"] = work_branch

    local_repo = _best_effort(
        lambda: git.Repo(state["repo_local_path"]), "could not open the job workspace"
    )

    had_changes = False
    push_error: str | None = None
    stat_text = ""
    files: list[str] = []
    pr_url: str | None = None
    pr_number: int | None = None

    if local_repo is not None:
        had_changes = bool(
            _best_effort(
                lambda: commit_all(
                    local_repo,
                    build_commit_message(
                        issue_number=state["issue_number"],
                        issue_title=state["issue_title"],
                        summary=state["commit_message"],
                    ),
                ),
                "could not commit the partial work",
                job_id=state.get("job_id"),
            )
        )

        if had_changes:
            stat_text = diff_stat(local_repo, state["base_branch"])
            files = changed_files(local_repo, state["base_branch"])
            try:
                push_branch(
                    local_repo, work_branch, get_auth_provider().get_token(), state["repo_full_name"]
                )
            except Exception as exc:  # noqa: BLE001 -- reported to the human below
                push_error = str(exc)
                logger.warning(
                    "could not push the partial work",
                    job_id=state.get("job_id"),
                    branch=work_branch,
                    error=push_error,
                )
        else:
            # Mirrors pr_creation's no-changes case, but stays needs_human --
            # downgrading to "failed" would drop it out of the human queue.
            result["error_log"] = state["error_log"] + ["gave up before making any file changes"]

    client = _best_effort(get_client, "could not reach GitHub")
    gh_repo = (
        _best_effort(lambda: get_repo(client, state["repo_full_name"]), "could not open the repo")
        if client is not None
        else None
    )
    if gh_repo is None:
        return result

    plan_summary = "\n".join(
        f"- [{s['action']}] `{s['file']}`: {s['description']}" for s in state["plan_steps"]
    )
    attempts = _attempts_table(state)

    def render(url: str | None, number: int | None) -> str:
        return build_escalation_comment(
            debug_analysis=state["debug_analysis"],
            test_history_summary=attempts,
            reason=reason,
            iteration_count=state["iteration_count"],
            max_iterations=state["max_iterations"],
            cost_spent_usd=budget.total_cost_usd if budget else None,
            cost_budget_usd=budget.budget_usd if budget else None,
            job_id=state["job_id"],
            plan_summary=plan_summary,
            diff_stat_text=stat_text,
            files_changed=files,
            last_failure_output=(last["stdout"] or last["stderr"]) if last else "",
            pr_url=url,
            pr_number=number,
            work_branch=work_branch,
            push_error=push_error,
            had_changes=had_changes,
            injection_findings=state.get("injection_findings"),
        )

    if had_changes and push_error is None and settings.escalation_open_draft_pr:
        title = f"[needs human] Fix #{state['issue_number']}: {state['issue_title']}"[:72]
        pr = _open_draft_pr(gh_repo, title=title, body=render(None, None),
                            head=work_branch, base=state["base_branch"])
        if pr is not None:
            pr_url, pr_number = pr.html_url, pr.number
            result["pr_url"] = pr_url

    _best_effort(
        lambda: comment_on_issue(gh_repo, state["issue_number"], render(pr_url, pr_number)),
        "could not post the handoff comment",
        job_id=state.get("job_id"),
    )
    _best_effort(
        lambda: add_label(gh_repo, state["issue_number"], "agent:needs-human"),
        "could not label the issue",
    )

    logger.info(
        "handed off to a human",
        job_id=state.get("job_id"),
        reason=reason,
        branch=work_branch,
        pr_url=pr_url,
        pushed=push_error is None and had_changes,
    )
    return result


def _open_draft_pr(gh_repo, *, title: str, body: str, head: str, base: str):
    """Draft first, plain PR as a fallback.

    Some repositories and plans reject drafts with a 422; a normal PR still
    hands the work over, so retry once rather than losing the artifact. Kept
    here rather than in create_pr, which stays a thin passthrough.
    """
    try:
        return create_pr(gh_repo, title=title, body=body, head=head, base=base, draft=True)
    except GithubException as exc:
        logger.warning("draft PR rejected, retrying without draft", error=str(exc))
    return _best_effort(
        lambda: create_pr(gh_repo, title=title, body=body, head=head, base=base),
        "could not open a pull request for the partial work",
    )
