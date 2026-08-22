"""The give-up path had no coverage at all, which is how it went unnoticed
that it never committed or pushed the agent's work -- the code existed only as
a dirty working tree on whichever machine ran the job.

Git operations run against a real throwaway repo; GitHub is mocked.
"""

from unittest.mock import MagicMock

import git
import pytest
from github.GithubException import GithubException

from src.graph.nodes import escalation as esc
from src.graph.nodes.escalation import human_escalation_node


@pytest.fixture
def workspace(tmp_path):
    """A clone-like repo with a base commit, a work branch, and a dirty edit --
    the shape the agent leaves behind when it gives up."""
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "test")
        cfg.set_value("user", "email", "test@example.com")
    (tmp_path / "app.py").write_text("def discount(p, n):\n    return p - n\n")
    repo.git.add(A=True)
    repo.index.commit("initial")
    repo.git.branch("main-base")
    repo.git.checkout("-b", "agent/issue-42-abc12345")
    (tmp_path / "app.py").write_text("def discount(p, n):\n    return p * (1 - n / 100)\n")
    return repo


@pytest.fixture
def github(monkeypatch):
    """Mocked GitHub surface. Returns the repo mock so tests can assert calls."""
    gh_repo = MagicMock()
    pr = MagicMock()
    pr.html_url = "https://github.com/o/r/pull/7"
    pr.number = 7
    gh_repo.get_pulls.return_value = iter([])
    gh_repo.owner.login = "o"
    gh_repo.create_pull.return_value = pr

    monkeypatch.setattr(esc, "get_client", lambda: MagicMock())
    monkeypatch.setattr(esc, "get_repo", lambda client, name: gh_repo)
    monkeypatch.setattr(esc, "push_branch", MagicMock())
    monkeypatch.setattr(esc, "comment_on_issue", MagicMock())
    monkeypatch.setattr(esc, "add_label", MagicMock())
    monkeypatch.setattr(
        esc, "get_auth_provider", lambda: MagicMock(get_token=lambda: "tok")
    )
    return gh_repo


def _state(workspace, **overrides):
    base = {
        "job_id": "a1b2c3d4-e5f6-0000-0000-000000000000",
        "repo_local_path": str(workspace.working_dir),
        "repo_full_name": "o/r",
        "issue_number": 42,
        "issue_title": "discount is applied as a flat amount",
        "issue_body": "",
        "base_branch": "main-base",
        "work_branch": "agent/issue-42-abc12345",
        "plan_steps": [{"action": "edit", "file": "app.py", "description": "use a percentage"}],
        "commit_message": "switched to a percentage",
        "debug_analysis": "price arrives as a str from load_catalog()",
        "test_result": {
            "command": "pytest -q", "exit_code": 1, "passed": False,
            "stdout": "E assert 90.0 == 85.0", "stderr": "", "failure_signature": "assert 90.0 == 85.0",
        },
        "test_history": [
            {"command": "pytest -q", "exit_code": 1, "passed": False, "stdout": "", "stderr": "",
             "failure_signature": "assert 90.0 == 85.0"},
            {"command": "pytest -q", "exit_code": 1, "passed": False, "stdout": "", "stderr": "",
             "failure_signature": "assert 90.0 == 85.0"},
        ],
        "iteration_count": 2,
        "max_iterations": 6,
        "error_log": [],
        "injection_findings": {},
    }
    base.update(overrides)
    return base


def test_commits_pushes_and_opens_a_draft_pr(workspace, github):
    """The regression test for the core bug: the work must actually leave the
    machine. Before this change nothing was ever committed."""
    out = human_escalation_node(_state(workspace))

    assert not workspace.is_dirty(), "the agent's edits must be committed"
    assert "switched to a percentage" in workspace.head.commit.message
    esc.push_branch.assert_called_once()
    assert github.create_pull.call_args.kwargs["draft"] is True
    assert out["pr_url"] == "https://github.com/o/r/pull/7"
    assert out["status"] == "needs_human"


def test_the_comment_explains_why_and_links_the_pr(workspace, github):
    human_escalation_node(_state(workspace))

    body = esc.comment_on_issue.call_args.args[2]
    assert "going in circles" in body, "two identical failures = repeating_failure"
    assert "More attempts won't help" in body
    assert "https://github.com/o/r/pull/7" in body
    assert "price arrives as a str" in body, "the debugger's analysis must survive"
    assert "app.py" in body


def test_local_run_touches_no_github(workspace, github):
    out = human_escalation_node(_state(workspace, repo_full_name=""))

    assert out == {"status": "needs_human"}
    esc.comment_on_issue.assert_not_called()
    esc.push_branch.assert_not_called()


def test_still_needs_human_when_the_comment_fails(workspace, github):
    """A token without Issues:write must not cost us the whole handoff -- and
    must not leave the job non-terminal."""
    esc.comment_on_issue.side_effect = GithubException(403, "no", {})

    out = human_escalation_node(_state(workspace))

    assert out["status"] == "needs_human"
    assert out["pr_url"] == "https://github.com/o/r/pull/7"


def test_push_failure_still_comments_and_names_the_local_branch(workspace, github):
    esc.push_branch.side_effect = git.GitCommandError("push", 128, b"permission denied")

    out = human_escalation_node(_state(workspace))

    assert out["status"] == "needs_human"
    assert "pr_url" not in out, "no PR without a pushed branch"
    body = esc.comment_on_issue.call_args.args[2]
    assert "agent/issue-42-abc12345" in body
    assert "couldn't push" in body


def test_nothing_to_commit_skips_push_and_pr_but_still_hands_off(workspace, github):
    workspace.git.checkout("--", ".")  # discard the agent's edit

    out = human_escalation_node(_state(workspace))

    assert out["status"] == "needs_human", "must not downgrade to failed"
    assert "gave up before making any file changes" in out["error_log"]
    esc.push_branch.assert_not_called()
    assert "no code to hand over" in esc.comment_on_issue.call_args.args[2]


def test_falls_back_to_a_normal_pr_when_drafts_are_rejected(workspace, github):
    github.create_pull.side_effect = [
        GithubException(422, "drafts not supported", {}),
        MagicMock(html_url="https://github.com/o/r/pull/8", number=8),
    ]

    out = human_escalation_node(_state(workspace))

    assert out["pr_url"] == "https://github.com/o/r/pull/8"
    assert github.create_pull.call_count == 2
    assert github.create_pull.call_args_list[1].kwargs.get("draft", False) is False


def test_draft_pr_can_be_turned_off(workspace, github, monkeypatch):
    monkeypatch.setattr(esc.settings, "escalation_open_draft_pr", False)

    out = human_escalation_node(_state(workspace))

    github.create_pull.assert_not_called()
    esc.push_branch.assert_called_once(), "branch still pushed"
    assert "pr_url" not in out
    assert "agent/issue-42-abc12345" in esc.comment_on_issue.call_args.args[2]


def test_a_broken_workspace_does_not_lose_terminality(workspace, github):
    """Whatever goes wrong, the node must return a terminal status -- run_job
    catches only TimeoutError, so a raise here leaves the job row stuck at
    'testing' with a null completed_at forever."""
    out = human_escalation_node(_state(workspace, repo_local_path="/nonexistent"))

    assert out["status"] == "needs_human"


def test_hands_off_without_spending_a_single_token(workspace, github, monkeypatch):
    """One of the give-up conditions IS budget exhaustion, so this path must
    never call a model. call_structured has no budget check to stop it."""
    def explode(*a, **k):
        raise AssertionError("the escalation path must not make model calls")

    monkeypatch.setattr("src.agents.usage.record_usage", explode)
    human_escalation_node(_state(workspace))
