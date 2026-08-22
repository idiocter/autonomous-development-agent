"""Real-world write access lives here: branches, commits, PRs, comments.
Every git operation (branch/commit/push) goes through GitPython against the
job's local clone; every GitHub API call (issue reading, PR/comment
creation) goes through PyGithub. The agent never gets raw credentials to the
host's git remotes -- only this module holds the token, scoped to exactly
what get_auth_provider() returns.
"""

import textwrap
from dataclasses import dataclass

import git
import structlog
from github import Github
from github.GithubException import GithubException
from github.Issue import Issue
from github.PullRequest import PullRequest
from github.Repository import Repository

from src.github_integration.auth import get_auth_provider
from src.security.prompt_guard import injection_warning_block, redact_secrets
from src.tools.filesystem_tools import is_test_path

logger = structlog.get_logger(__name__)

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

# Secret-shaped files must never reach a commit. filesystem_tools already
# refuses to *read* these, but `git add -A` is a second door onto the same
# risk and it doesn't go through those tools at all: a repo's own test run can
# create a .env in the workspace (this project's test suite does exactly that),
# and the agent pushes to a branch on a repo that is often public. Unstaging is
# the safe failure mode -- the file stays in the working tree, it just never
# leaves the machine.
#
# .env.example and friends are deliberately excluded from the sweep: they are
# meant to be committed, contain no live values, and are commonly edited.
_SECRET_PATHSPECS = [
    ":(glob)**/.env",
    ":(glob)**/.env.*",
    ":(glob)**/id_rsa*",
    ":(glob)**/id_ed25519*",
    ":(glob)**/.npmrc",
    ":(glob)**/.pypirc",
    ":(glob)**/.netrc",
    ":(glob)**/*.pem",
    ":(glob)**/*.key",
    ":(glob)**/*.p12",
    ":(glob)**/*.pfx",
    ":(glob)**/*.keystore",
    ":(glob)**/*.jks",
    ":(glob)**/credentials.json",
    ":(glob)**/service_account.json",
    ":(glob)**/secrets.yaml",
    ":(glob)**/secrets.yml",
    ":(glob,exclude)**/.env.example",
    ":(glob,exclude)**/.env.sample",
    ":(glob,exclude)**/.env.template",
]


def commit_all(repo: git.Repo, message: str) -> bool:
    """Returns False if there was nothing to commit (working tree clean)."""
    repo.git.add(A=True)

    # A staged secret is worth knowing about even though it's about to be
    # dropped -- it means something in the run created one.
    try:
        staged_secrets = repo.git.diff("--cached", "--name-only", "--", *_SECRET_PATHSPECS)
        if staged_secrets.strip():
            logger.warning(
                "refusing to commit secret-shaped files staged by git add -A",
                paths=staged_secrets.split("\n"),
            )
    except git.GitCommandError:
        pass

    # Drop artifacts and secrets back out of the index. `git reset -- <pathspec>`
    # is a no-op when nothing matches, so this is safe on a clean tree.
    try:
        repo.git.reset("--", *_ARTIFACT_PATHSPECS, *_SECRET_PATHSPECS)
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
    draft: bool = False,
) -> PullRequest:
    """Opens a PR, or returns the open one already on this branch.

    `draft=True` is used by the escalation path to hand over partial work: a
    draft says "not ready to merge", which is exactly true of a run that gave
    up, and branch protection generally refuses to merge one.

    Note the existing-PR branch returns it *unchanged* -- title and body are
    not refreshed. Branch names carry the job id so collisions are rare, and
    the escalation path posts a fresh issue comment as its source of truth.
    """
    existing = find_existing_pr(repo, head)
    if existing is not None:
        return existing
    return repo.create_pull(
        title=title, body=redact_secrets(body), head=head, base=base, draft=draft
    )


def comment_on_issue(repo: Repository, issue_number: int, body: str) -> None:
    issue = repo.get_issue(issue_number)
    issue.create_comment(redact_secrets(body))


def add_label(repo: Repository, issue_number: int, label: str) -> None:
    issue = repo.get_issue(issue_number)
    try:
        issue.add_to_labels(label)
    except GithubException:
        # Label may not exist in the target repo -- non-fatal, the comment
        # itself is the important part.
        pass


def is_test_file(path: str) -> bool:
    """Flags test edits for human review.

    Same definition the filesystem tools use to *refuse* the write, imported
    rather than restated: if the two ever disagreed, the gap between them is
    exactly the set of files that get edited without anyone being told.
    """
    return is_test_path(path)


_SUBJECT_LIMIT = 72


def build_commit_message(
    *,
    issue_number: int | None,
    issue_title: str,
    summary: str | None,
) -> str:
    """Format the coding agent's prose into an actual commit message.

    The coder returns a paragraph -- "I fixed the apply_discount function in
    inventory.py to subtract a percentage of the price rather than a flat
    amount, by changing the return statement to..." -- and that string used to
    be handed to `git commit` whole. The result was 300-character subject lines
    in the target repo, which is what `git log --oneline`, the PR's commit list
    and every blame view show. The prose is worth keeping; it belongs in the
    body, under a subject someone can scan.

    Redacted like any other outbound text: this lands in a repo that is often
    public, and the summary is model output that could quote a secret it read.
    Redaction runs *before* wrapping, not after -- textwrap will happily split
    a 51-character API key across two lines, and a key with a newline in the
    middle of it matches none of the patterns while remaining perfectly
    readable to anyone looking at the commit.
    """
    title = " ".join(redact_secrets(issue_title or "").split())
    if issue_number is not None:
        subject = f"Fix #{issue_number}: {title}" if title else f"Fix issue #{issue_number}"
    else:
        subject = title or "Apply automated fix"

    if len(subject) > _SUBJECT_LIMIT:
        subject = subject[: _SUBJECT_LIMIT - 3].rstrip() + "..."

    body_source = " ".join(redact_secrets(summary or "").split())
    if not body_source:
        return subject

    return f"{subject}\n\n{textwrap.fill(body_source, width=_SUBJECT_LIMIT)}"


def build_pr_body(
    *,
    issue_number: int,
    plan_summary: str,
    files_changed: list[str],
    test_command: str,
    test_passed: bool,
    injection_findings: dict[str, list[str]] | None = None,
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

    # Injection attempts in the issue text are detected during planning; the
    # reviewer of *this* PR is the person who needs to know about them, so the
    # finding travels here rather than stopping at the log line.
    warning += injection_warning_block(injection_findings or {})

    return (
        f"Resolves #{issue_number}.\n\n"
        f"**This PR was opened autonomously by autonomous-dev-agent.**\n"
        f"{warning}\n"
        f"### Plan\n{plan_summary}\n\n"
        f"### Files changed\n{files_list}\n\n"
        f"### Tests\n`{test_command}` -- {status}\n"
    )


def diff_stat(repo: git.Repo, base_branch: str) -> str:
    """`git diff --stat` against the base, or "" if git can't answer."""
    try:
        return repo.git.diff("--stat", f"{base_branch}...HEAD")
    except git.GitCommandError:
        return ""


def changed_files(repo: git.Repo, base_branch: str) -> list[str]:
    """Files actually changed, as opposed to the files the plan intended to
    change. At give-up time those two often diverge, and the gap is itself
    worth showing a human.
    """
    try:
        out = repo.git.diff("--name-only", f"{base_branch}...HEAD")
    except git.GitCommandError:
        return []
    return [line for line in out.splitlines() if line.strip()]


def _fence(text: str, limit: int = 3000) -> str:
    """Fence untrusted output so its own backticks can't break out of the block.

    Test output is arbitrary text from someone else's suite -- it can contain
    any number of consecutive backticks, so pick a fence longer than the
    longest run in it.
    """
    text = text.strip()
    if len(text) > limit:
        text = text[-limit:]
        text = "…(truncated)\n" + text[text.find("\n") + 1 :]
    longest = 0
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


# What each guard means for the person picking the work up. The next-step line
# matters more than the headline: "raise the budget" and "more attempts won't
# help" are opposite advice, and reporting the wrong one wastes their time.
_GIVE_UP_WORDING: dict[str, tuple[str, str]] = {
    "over_budget": (
        "I hit this job's cost budget.",
        (
            "Raise `JOB_COST_BUDGET_USD` and re-run to give it more room, or take "
            "over from the branch below — it was still making progress when the "
            "money ran out."
        ),
    ),
    "repeating_failure": (
        "The same test failure came back twice in a row, so I was going in circles.",
        (
            "More attempts won't help — it's stuck on one error and probably needs "
            "a piece of context I don't have. Start from the diff and the last "
            "failing output below."
        ),
    ),
    "iteration_cap": (
        "I used all my attempts.",
        (
            "Here's how far it got. If the failures were still changing between "
            "attempts (see the table), raising `MAX_ITERATIONS` may be enough."
        ),
    ),
}


def build_escalation_comment(
    *,
    debug_analysis: str | None,
    test_history_summary: str,
    reason: str | None = None,
    iteration_count: int = 0,
    max_iterations: int = 0,
    cost_spent_usd: float | None = None,
    cost_budget_usd: float | None = None,
    job_id: str = "",
    plan_summary: str = "",
    diff_stat_text: str = "",
    files_changed: list[str] | None = None,
    last_failure_output: str = "",
    pr_url: str | None = None,
    pr_number: int | None = None,
    work_branch: str | None = None,
    push_error: str | None = None,
    had_changes: bool = True,
    injection_findings: dict[str, list[str]] | None = None,
) -> str:
    """The handoff document, rendered for both the issue comment and the PR body.

    Every argument past the first two has a default, so old call sites keep
    working and the node can pass only what it managed to gather.

    Sections are omitted entirely when their input is empty rather than
    rendering "(none)" -- a document full of empty headings reads like a stub,
    which is what made the original version feel like a shrug.

    Deliberately not built on build_pr_body: that hardcodes "Resolves #N", and
    on partial work that's dangerous -- merging it would close an issue nobody
    fixed.
    """
    headline, next_step = _GIVE_UP_WORDING.get(
        reason or "",
        (
            "I couldn't get the tests passing.",
            "Start from the diff and the last failing output below.",
        ),
    )
    if reason == "iteration_cap" and max_iterations:
        headline = f"I used all {max_iterations} of my attempts."
    if reason == "over_budget" and cost_spent_usd is not None and cost_budget_usd:
        headline = (
            f"I hit this job's cost budget "
            f"(${cost_spent_usd:.2f} of ${cost_budget_usd:.2f} spent)."
        )

    out = ["🤖 **I couldn't finish this one — here's everything I have.**", "", f"**{headline}**"]

    if not had_changes:
        out.append("I stopped before making any file changes, so there's no code to hand over.")
    elif pr_url:
        out.append("The work so far is pushed and open as a draft PR. It does **not** pass tests.")
    elif push_error:
        out.append(
            f"The work is committed locally on branch `{work_branch}` in the job workspace, "
            f"but I couldn't push it: `{push_error}`"
        )
    elif work_branch:
        out.append(f"The work so far is pushed to branch `{work_branch}`. It does **not** pass tests.")

    if pr_url:
        label = f"#{pr_number} — draft" if pr_number else "draft PR"
        out += ["", f"**→ [{label}]({pr_url})**" + (f" · branch `{work_branch}`" if work_branch else "")]

    # Test-file edits and injection attempts matter more here than on a green
    # PR: partial work under review is exactly where a weakened assertion or a
    # successful injection would go unnoticed.
    touched_tests = [f for f in (files_changed or []) if is_test_file(f)]
    if touched_tests:
        listed = "\n".join(f"> - `{f}`" for f in touched_tests)
        out += [
            "",
            "> [!WARNING]",
            "> **This changes test files.** The agent is instructed not to, so check these",
            "> aren't just weakened assertions:",
            listed,
        ]
    warning = injection_warning_block(injection_findings or {})
    if warning:
        out.append(warning)

    out += ["", "### What to do next", next_step]

    if plan_summary.strip():
        out += ["", "### What I was trying to do", plan_summary.strip()]
    if diff_stat_text.strip():
        out += ["", "### What I actually changed", _fence(diff_stat_text)]
    if test_history_summary.strip():
        out += ["", "### What I tried, and what happened", test_history_summary.strip()]
    if debug_analysis and debug_analysis.strip():
        quoted = "\n".join(f"> {line}" for line in debug_analysis.strip().splitlines())
        out += ["", "### My last read on it", quoted]
    if last_failure_output.strip():
        out += [
            "",
            "<details><summary>Last failing output</summary>",
            "",
            _fence(last_failure_output),
            "",
            "</details>",
        ]

    footer = []
    if iteration_count and max_iterations:
        footer.append(f"{iteration_count} of {max_iterations} attempts")
    if cost_spent_usd is not None and cost_budget_usd:
        footer.append(f"${cost_spent_usd:.2f} of ${cost_budget_usd:.2f} spent")
    if job_id:
        footer.append(f"job `{job_id[:8]}`")
    if footer:
        out += ["", f"<sub>{' · '.join(footer)}</sub>"]

    return "\n".join(out) + "\n"
