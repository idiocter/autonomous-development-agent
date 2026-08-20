"""Phase 2 CLI: runs the full agent loop against a real GitHub issue --
clones the repo, works the issue, and opens a real PR. Needs GITHUB_TOKEN
and ANTHROPIC_API_KEY set in .env, and a scratch/test repo to target (never
point this at a repo you care about until you trust it).

Usage:
    uv run python scripts/run_github_job.py --repo owner/repo --issue 42
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402
from src.github_integration.auth import get_auth_provider  # noqa: E402
from src.graph.state import AgentState  # noqa: E402
from src.worker.job_runner import run_job  # noqa: E402
from src.logging_config import configure_logging  # noqa: E402
from src.tools.github_tools import (  # noqa: E402
    clone_repo,
    create_work_branch,
    get_client,
    get_issue_context,
    get_repo,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent loop against a real GitHub issue.")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--issue", type=int, required=True, help="Issue number")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--max-iterations", type=int, default=6)
    args = parser.parse_args()

    configure_logging()

    token = get_auth_provider().get_token()
    client = get_client()
    gh_repo = get_repo(client, args.repo)
    issue_ctx = get_issue_context(gh_repo, args.issue)

    job_id = str(uuid.uuid4())
    # settings.workspace_dir, not the system temp dir -- Docker Desktop on
    # Mac doesn't reliably bind-mount /tmp/private-var, only /Users paths.
    scratch = Path(settings.workspace_dir).resolve() / f"job-{job_id[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    local_path = scratch / args.repo.split("/")[-1]
    print(f"Cloning {args.repo} into {local_path}")
    local_repo = clone_repo(args.repo, str(local_path), token)

    work_branch = f"agent/issue-{args.issue}-{job_id[:8]}"
    create_work_branch(local_repo, args.base_branch, work_branch)

    initial_state: AgentState = {
        "job_id": job_id,
        "repo_local_path": str(local_path),
        "issue_title": issue_ctx.title,
        "issue_body": issue_ctx.body,
        "repo_full_name": args.repo,
        "issue_number": args.issue,
        "base_branch": args.base_branch,
        "work_branch": work_branch,
        "plan_steps": [],
        "relevant_context": [],
        "file_diffs": [],
        "commit_message": None,
        "test_command": "",
        "test_result": None,
        "test_history": [],
        "debug_analysis": None,
        "iteration_count": 0,
        "max_iterations": args.max_iterations,
        "status": "planning",
        "error_log": [],
        "pr_url": None,
    }

    # Same wrapper as the webhook worker path -- this previously bypassed
    # run_job entirely, so it had neither cost tracking nor the wall-clock
    # timeout guard, despite being the path that opens real PRs.
    final_state = asyncio.run(run_job(initial_state, triggered_by="manual:cli"))

    print(f"\n--- final status: {final_state['status']} ---")
    if final_state["pr_url"]:
        print(f"PR: {final_state['pr_url']}")
    elif final_state["status"] == "needs_human":
        print(f"Escalated to a human on issue #{args.issue} -- see the comment there.")


if __name__ == "__main__":
    main()
