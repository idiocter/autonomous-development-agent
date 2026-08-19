"""Real-world write access lives here: branches, commits, PRs, comments.
Every git operation (branch/commit/push) goes through GitPython against the
job's local clone; every GitHub API call (issue reading, PR/comment
creation) goes through PyGithub. The agent never gets raw credentials to the
host's git remotes -- only this module holds the token, scoped to exactly
what get_auth_provider() returns.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath

import git
from github import Github
from github.GithubException import GithubException
from github.Issue import Issue
from github.PullRequest import PullRequest
from github.Repository import Repository

from src.github_integration.auth import get_auth_provider

BOT_NAME = "autonomous-dev-agent[bot]"
BOT_EMAIL = "autonomous-dev-agent-bot@users.noreply.github.com"


@dataclass
class IssueContext:
    number: int
    title: str
    body: str
    comments: list[str]


def get_client() -> Github:
    return Github(get_auth_provider().get_token())


def get_repo(client: Github, repo_full_name: str) -> Repository:
    return client.get_repo(repo_full_name)


def get_issue_context(repo: Repository, issue_number: int) -> IssueContext:
    issue: Issue = repo.get_issue(issue_number)
    comments = [c.body for c in issue.get_comments()]
    return IssueContext(number=issue.number, title=issue.title, body=issue.body or "", comments=comments)


def clone_repo(repo_full_name: str, dest_path: str, token: str) -> git.Repo:
    url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"
    return git.Repo.clone_from(url, dest_path)


def create_work_branch(repo: git.Repo, base_branch: str, work_branch: str) -> None:
    repo.git.checkout(base_branch)
    repo.git.pull()
    if work_branch in [h.name for h in repo.heads]:
        repo.git.checkout(work_branch)
    else:
        repo.git.checkout("-b", work_branch)


# Build artifacts the sandbox test run leaves behind in the workspace. A
# blind `git add -A` sweeps these into the PR when the target repo has no
# .gitignore covering them -- observed for real: a first live PR carried two
# __pycache__/*.pyc files alongside the one-line fix. Unstaged rather than
# .gitignore'd, since writing a .gitignore into someone's repo is a change
# they didn't ask for.
_ARTIFACT_PATHSPECS = [
    ":(glob)**/__pycache__/**",
    ":(glob)**/*.py[co]",
    ":(glob)**/.pytest_cache/**",
    ":(glob)**/.mypy_cache/**",
    ":(glob)**/.ruff_cache/**",
    ":(glob)**/.DS_Store",
    ":(glob)**/node_modules/**",
]


def commit_all(repo: git.Repo, message: str) -> bool:
    """Returns False if there was nothing to commit (working tree clean)."""
    repo.git.add(A=True)

    # Drop artifacts back out of the index. `git reset -- <pathspec>` is a
    # no-op when nothing matches, so this is safe on a clean tree.
    try:
        repo.git.reset("--", *_ARTIFACT_PATHSPECS)
    except git.GitCommandError:
        # Never let artifact cleanup abort an otherwise valid commit.
        pass

    if not repo.index.diff("HEAD"):
        return False
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", BOT_NAME)
        cfg.set_value("user", "email", BOT_EMAIL)
    repo.index.commit(message)
    return True


def push_branch(repo: git.Repo, work_branch: str, token: str, repo_full_name: str) -> None:
    url = f"https://x-access-token:{token}@github.com/{repo_full_name}.git"
    remote = repo.remote("origin")
    remote.set_url(url)
    remote.push(refspec=f"{work_branch}:{work_branch}")


def find_existing_pr(repo: Repository, work_branch: str) -> PullRequest | None:
    owner = repo.owner.login
    open_prs = repo.get_pulls(state="open", head=f"{owner}:{work_branch}")
    for pr in open_prs:
        return pr
    return None


def create_pr(
    repo: Repository,
    *,
    title: str,
    body: str,
    head: str,
    base: str,
) -> PullRequest:
    existing = find_existing_pr(repo, head)
    if existing is not None:
        return existing
    return repo.create_pull(title=title, body=body, head=head, base=base)


def comment_on_issue(repo: Repository, issue_number: int, body: str) -> None:
    issue = repo.get_issue(issue_number)
    issue.create_comment(body)


def add_label(repo: Repository, issue_number: int, label: str) -> None:
    issue = repo.get_issue(issue_number)
    try:
        issue.add_to_labels(label)
    except GithubException:
        # Label may not exist in the target repo -- non-fatal, the comment
        # itself is the important part.
        pass


def is_test_file(path: str) -> bool:
    """Heuristic used to flag test edits for human review. Deliberately
    broad -- a false positive costs a reviewer one glance, a false negative
    lets a weakened assertion through unnoticed.
    """
    name = PurePosixPath(path).name.lower()
    parts = {p.lower() for p in PurePosixPath(path).parts}
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
        or bool({"tests", "test", "__tests__", "spec"} & parts)
    )


def build_pr_body(
    *,
    issue_number: int,
    plan_summary: str,
    files_changed: list[str],
    test_command: str,
    test_passed: bool,
) -> str:
    files_list = "\n".join(f"- `{f}`" for f in files_changed) or "(no files listed)"
    status = "✅ passing" if test_passed else "⚠️ not verified"

    # The agents are instructed never to touch tests, but instructions aren't
    # enforcement -- surface it loudly rather than trusting the prompt, since
    # "made the test pass by editing the test" is the failure mode that most
    # easily slips through review.
    touched_tests = [f for f in files_changed if is_test_file(f)]
    warning = ""
    if touched_tests:
        listed = "\n".join(f"- `{f}`" for f in touched_tests)
        warning = (
            "\n> [!WARNING]\n"
            "> **This PR modifies test files.** The agent is instructed not to, so\n"
            "> review these closely and confirm the change isn't just making a\n"
            "> failing assertion pass:\n"
            + "\n".join(f"> {line}" for line in listed.splitlines())
            + "\n"
        )

    return (
        f"Resolves #{issue_number}.\n\n"
        f"**This PR was opened autonomously by autonomous-dev-agent.**\n"
        f"{warning}\n"
        f"### Plan\n{plan_summary}\n\n"
        f"### Files changed\n{files_list}\n\n"
        f"### Tests\n`{test_command}` -- {status}\n"
    )


def build_escalation_comment(*, debug_analysis: str | None, test_history_summary: str) -> str:
    return (
        "🤖 I attempted to resolve this issue but couldn't get tests passing within "
        "the allotted attempts, so I'm stopping here for a human to take a look.\n\n"
        f"### Attempts\n{test_history_summary}\n\n"
        f"### Last debugging analysis\n{debug_analysis or '(none)'}\n"
    )
