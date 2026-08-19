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
    build_escalation_comment,
    build_pr_body,
    commit_all,
    create_pr,
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
    mock_repo.create_pull.assert_called_once_with(title="t", body="b", head="h", base="main")


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
