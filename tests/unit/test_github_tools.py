"""Local git operations (branch/commit) are tested against a real throwaway
git repo -- no token or network needed. GitHub API operations (PR/comment
creation) are tested with mocked PyGithub objects since there's no live
token in this environment yet; see plan.md's credential status.
"""

from unittest.mock import MagicMock

import git
import pytest

from src.tools.github_tools import (
    BOT_EMAIL,
    BOT_NAME,
    build_commit_message,
    build_escalation_comment,
    build_pr_body,
    changed_files,
    commit_all,
    create_pr,
    diff_stat,
    find_existing_pr,
)


@pytest.fixture
def local_repo(tmp_path):
    repo = git.Repo.init(tmp_path)
    (tmp_path / "file.txt").write_text("hello\n")
    repo.git.add(A=True)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "test")
        cfg.set_value("user", "email", "test@example.com")
    repo.index.commit("initial commit")
    return repo


def test_commit_all_commits_dirty_changes(local_repo, tmp_path):
    (tmp_path / "file.txt").write_text("changed\n")
    had_changes = commit_all(local_repo, "fix: update file")

    assert had_changes is True
    assert local_repo.head.commit.message == "fix: update file"
    assert local_repo.head.commit.author.name == BOT_NAME
    assert local_repo.head.commit.author.email == BOT_EMAIL


def test_commit_all_returns_false_when_clean(local_repo):
    had_changes = commit_all(local_repo, "no-op commit")
    assert had_changes is False


def test_commit_all_commits_new_untracked_file(local_repo, tmp_path):
    (tmp_path / "new_file.txt").write_text("new\n")
    had_changes = commit_all(local_repo, "feat: add new file")

    assert had_changes is True
    assert "new_file.txt" in local_repo.git.show("--stat", "HEAD")


def test_find_existing_pr_returns_none_when_no_open_prs():
    mock_repo = MagicMock()
    mock_repo.owner.login = "acme"
    mock_repo.get_pulls.return_value = iter([])

    result = find_existing_pr(mock_repo, "agent/issue-1-abc123")

    assert result is None
    mock_repo.get_pulls.assert_called_once_with(state="open", head="acme:agent/issue-1-abc123")


def test_find_existing_pr_returns_first_match():
    mock_pr = MagicMock()
    mock_repo = MagicMock()
    mock_repo.owner.login = "acme"
    mock_repo.get_pulls.return_value = iter([mock_pr])

    result = find_existing_pr(mock_repo, "agent/issue-1-abc123")

    assert result is mock_pr


def test_create_pr_is_idempotent_when_pr_already_open():
    existing_pr = MagicMock()
    mock_repo = MagicMock()
    mock_repo.owner.login = "acme"
    mock_repo.get_pulls.return_value = iter([existing_pr])

    result = create_pr(mock_repo, title="t", body="b", head="h", base="main")

    assert result is existing_pr
    mock_repo.create_pull.assert_not_called()


def test_create_pr_creates_when_none_open():
    new_pr = MagicMock()
    mock_repo = MagicMock()
    mock_repo.owner.login = "acme"
    mock_repo.get_pulls.return_value = iter([])
    mock_repo.create_pull.return_value = new_pr

    result = create_pr(mock_repo, title="t", body="b", head="h", base="main")

    assert result is new_pr
    # draft defaults to False -- pins that the normal success path keeps
    # opening a real PR now that the escalation path can ask for a draft.
    mock_repo.create_pull.assert_called_once_with(
        title="t", body="b", head="h", base="main", draft=False
    )


def test_create_pr_can_open_a_draft():
    new_pr = MagicMock()
    mock_repo = MagicMock()
    mock_repo.owner.login = "acme"
    mock_repo.get_pulls.return_value = iter([])
    mock_repo.create_pull.return_value = new_pr

    create_pr(mock_repo, title="t", body="b", head="h", base="main", draft=True)

    assert mock_repo.create_pull.call_args.kwargs["draft"] is True


def test_build_pr_body_includes_issue_link_and_files():
    body = build_pr_body(
        issue_number=42,
        plan_summary="- [edit] foo.py: fix bug",
        files_changed=["foo.py"],
        test_command="pytest -q",
        test_passed=True,
    )
    assert "#42" in body
    assert "foo.py" in body
    assert "pytest -q" in body
    assert "passing" in body


def test_build_escalation_comment_includes_debug_analysis():
    comment = build_escalation_comment(
        debug_analysis="root cause: off-by-one",
        test_history_summary="- attempt 1: FAIL",
    )
    assert "off-by-one" in comment
    assert "attempt 1" in comment


def test_is_test_file_detects_common_conventions():
    from src.tools.github_tools import is_test_file

    assert is_test_file("test_calculator.py")
    assert is_test_file("src/tests/helpers.py")
    assert is_test_file("pkg/foo_test.go")
    assert is_test_file("web/__tests__/app.js")
    assert is_test_file("ui/Button.test.tsx") or is_test_file("ui/Button.test.ts")


def test_is_test_file_ignores_ordinary_sources():
    from src.tools.github_tools import is_test_file

    assert not is_test_file("calculator.py")
    assert not is_test_file("src/latest.py")       # contains "test" but isn't one
    assert not is_test_file("src/contest/main.py")  # substring, not a path part


def test_pr_body_warns_when_tests_were_modified():
    body = build_pr_body(
        issue_number=7,
        plan_summary="- fix bug",
        files_changed=["calculator.py", "test_calculator.py"],
        test_command="pytest -q",
        test_passed=True,
    )
    assert "WARNING" in body
    assert "modifies test files" in body
    assert "test_calculator.py" in body


def test_pr_body_has_no_warning_for_source_only_changes():
    body = build_pr_body(
        issue_number=7,
        plan_summary="- fix bug",
        files_changed=["calculator.py"],
        test_command="pytest -q",
        test_passed=True,
    )
    assert "WARNING" not in body


def test_commit_all_excludes_build_artifacts(local_repo, tmp_path):
    (tmp_path / "real_change.py").write_text("x = 1\n")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "mod.cpython-312.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "stray.pyc").write_bytes(b"\x00\x01")

    assert commit_all(local_repo, "feat: real change") is True

    committed = local_repo.git.show("--name-only", "--pretty=format:", "HEAD").split()
    assert "real_change.py" in committed
    assert not any("__pycache__" in f or f.endswith(".pyc") for f in committed)


def test_commit_all_returns_false_when_only_artifacts_changed(local_repo, tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "mod.cpython-312.pyc").write_bytes(b"\x00\x01")

    assert commit_all(local_repo, "should not commit") is False


# `git add -A` doesn't go through filesystem_tools, so the read denylist gives
# no protection here. A repo's own test run creating a .env in the workspace is
# enough to push a live secret to a branch on a public repo.
@pytest.mark.parametrize(
    "secret_path",
    [
        ".env",
        ".env.local",
        "config/.env.production",
        "id_rsa",
        "certs/server.pem",
        "private.key",
        "credentials.json",
        "deploy/service_account.json",
        "secrets.yaml",
    ],
)
def test_commit_all_never_commits_secret_files(local_repo, tmp_path, secret_path):
    (tmp_path / "real_change.py").write_text("x = 1\n")
    target = tmp_path / secret_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("OPENAI_API_KEY=sk-proj-not-a-real-key-but-shaped-like-one\n")

    assert commit_all(local_repo, "feat: real change") is True

    committed = local_repo.git.show("--name-only", "--pretty=format:", "HEAD").split()
    assert "real_change.py" in committed
    assert secret_path not in committed
    # The file must survive on disk -- this is an unstage, not a delete.
    assert target.exists()


def test_commit_all_returns_false_when_only_a_secret_changed(local_repo, tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-proj-nope\n")

    assert commit_all(local_repo, "should not commit") is False


def test_commit_all_still_commits_env_example(local_repo, tmp_path):
    """.env.example carries no live values and is meant to be tracked."""
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=\n")

    assert commit_all(local_repo, "docs: add env example") is True
    committed = local_repo.git.show("--name-only", "--pretty=format:", "HEAD").split()
    assert ".env.example" in committed


def test_pr_body_warns_about_prompt_injection_findings():
    body = build_pr_body(
        issue_number=6,
        plan_summary="- [edit] inventory.py: round to 2dp",
        files_changed=["inventory.py"],
        test_command="pytest -q",
        test_passed=True,
        injection_findings={
            "issue": ["override_instructions", "credential_exfiltration"],
            "repo_context": [],
        },
    )

    assert "CAUTION" in body
    assert "prompt-injection" in body.lower()
    assert "override_instructions" in body
    assert "credential_exfiltration" in body


def test_pr_body_has_no_injection_warning_when_nothing_matched():
    body = build_pr_body(
        issue_number=6,
        plan_summary="- [edit] inventory.py: round to 2dp",
        files_changed=["inventory.py"],
        test_command="pytest -q",
        test_passed=True,
        injection_findings={"issue": [], "repo_context": []},
    )

    assert "CAUTION" not in body


# The coder returns prose. `git commit` needs a subject line. The gap between
# those two facts produced 300-character subjects in the sandbox repo, visible
# in every `git log --oneline` and PR commit list.
def test_commit_message_puts_prose_in_the_body_not_the_subject():
    summary = (
        "I fixed the apply_discount function in inventory.py to subtract a percentage "
        "of the price rather than a flat amount, by changing the return statement to: "
        "price * (1 - percent / 100). This matches the function's docstring and expected "
        "behavior tested by the existing test cases."
    )
    msg = build_commit_message(
        issue_number=6, issue_title="Round discount results to 2dp", summary=summary
    )
    subject, blank, body = msg.split("\n", 2)

    assert subject == "Fix #6: Round discount results to 2dp"
    assert blank == ""
    assert "apply_discount" in body
    assert all(len(line) <= 72 for line in msg.split("\n"))


def test_commit_message_truncates_an_overlong_subject():
    msg = build_commit_message(
        issue_number=12, issue_title="x" * 200, summary="did the thing"
    )
    subject = msg.split("\n", 1)[0]

    assert len(subject) <= 72
    assert subject.endswith("...")


def test_commit_message_without_a_summary_is_just_the_subject():
    msg = build_commit_message(issue_number=4, issue_title="Handle negative prices", summary=None)
    assert msg == "Fix #4: Handle negative prices"


def test_commit_message_for_a_local_run_has_no_issue_reference():
    msg = build_commit_message(issue_number=None, issue_title="Fix off-by-one", summary="done")
    assert msg.split("\n", 1)[0] == "Fix off-by-one"


def test_commit_message_redacts_secrets_quoted_by_the_coder():
    msg = build_commit_message(
        issue_number=6,
        issue_title="Round to 2dp",
        summary="I read the config, which contained sk-proj-" + "A" * 40,
    )
    assert "sk-proj-" + "A" * 40 not in msg
    assert "[REDACTED]" in msg


def test_commit_message_collapses_newlines_in_the_issue_title():
    msg = build_commit_message(
        issue_number=6, issue_title="Round\n\ndiscount results", summary="done"
    )
    assert msg.split("\n", 1)[0] == "Fix #6: Round discount results"


# --- the escalation handoff document -------------------------------------
#
# The give-up path used to post a fixed apology. These pin that it now says
# which guard tripped, what to do about it, and where the work went.

def _handoff(**overrides):
    base = {
        "debug_analysis": None,
        "test_history_summary": "| 1 | `pytest` | fail |",
        "reason": "iteration_cap",
        "iteration_count": 6,
        "max_iterations": 6,
        "work_branch": "agent/issue-42-abc",
    }
    base.update(overrides)
    return build_escalation_comment(**base)


def test_handoff_reports_the_budget_reason_and_its_next_step():
    body = _handoff(reason="over_budget", cost_spent_usd=2.01, cost_budget_usd=2.00)

    assert "$2.01 of $2.00 spent" in body
    assert "JOB_COST_BUDGET_USD" in body, "must tell them the knob to turn"
    assert "MAX_ITERATIONS" not in body, "wrong advice for a budget stop"


def test_handoff_reports_a_repeating_failure_as_stuck_not_out_of_attempts():
    body = _handoff(reason="repeating_failure")

    assert "going in circles" in body
    assert "More attempts won't help" in body


def test_handoff_reports_the_iteration_cap_with_the_real_number():
    body = _handoff(reason="iteration_cap", max_iterations=3)

    assert "all 3 of my attempts" in body
    assert "MAX_ITERATIONS" in body


def test_handoff_links_the_draft_pr():
    body = _handoff(pr_url="https://github.com/o/r/pull/7", pr_number=7)

    assert "https://github.com/o/r/pull/7" in body
    assert "#7" in body
    assert "does **not** pass tests" in body


def test_handoff_says_so_when_there_was_nothing_to_commit():
    body = _handoff(had_changes=False)

    assert "no code to hand over" in body


def test_handoff_names_the_local_branch_when_the_push_failed():
    body = _handoff(push_error="permission denied")

    assert "agent/issue-42-abc" in body
    assert "permission denied" in body
    assert "couldn't push" in body


def test_handoff_omits_empty_sections_rather_than_printing_none():
    """An empty heading reads like a stub; that's what made the old comment
    feel like a shrug."""
    body = _handoff(debug_analysis=None, plan_summary="", diff_stat_text="")

    assert "(none)" not in body
    assert "My last read on it" not in body
    assert "What I was trying to do" not in body
    assert "What I actually changed" not in body


def test_handoff_includes_the_debugger_analysis_when_there_is_one():
    body = _handoff(debug_analysis="price arrives as a str")

    assert "My last read on it" in body
    assert "> price arrives as a str" in body


def test_handoff_warns_when_test_files_were_touched():
    body = _handoff(files_changed=["src/app.py", "tests/test_app.py"])

    assert "[!WARNING]" in body
    assert "tests/test_app.py" in body
    assert "weakened assertions" in body


def test_handoff_carries_the_injection_warning():
    body = _handoff(injection_findings={"issue": ["credential_exfiltration"]})

    assert "CAUTION" in body
    assert "credential_exfiltration" in body


def test_handoff_fences_output_that_contains_backticks():
    """Test output is arbitrary text from someone else's suite -- its own
    backticks must not break out of the code block."""
    body = _handoff(last_failure_output="see ``` and ```` in here")

    assert "`````" in body


def test_handoff_truncates_enormous_test_output():
    body = _handoff(last_failure_output="x" * 20_000)

    assert len(body) < 6_000
    assert "truncated" in body


def test_handoff_footer_carries_attempts_cost_and_job_id():
    body = _handoff(iteration_count=4, max_iterations=6, cost_spent_usd=0.4,
                    cost_budget_usd=2.0, job_id="a1b2c3d4e5f6")

    assert "4 of 6 attempts" in body
    assert "job `a1b2c3d4`" in body


def test_diff_stat_and_changed_files_are_empty_on_git_error(local_repo):
    assert diff_stat(local_repo, "no-such-branch") == ""
    assert changed_files(local_repo, "no-such-branch") == []
