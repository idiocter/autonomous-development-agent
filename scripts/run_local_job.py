"""Phase 1 CLI: runs the full agent loop against a local repo, no GitHub or
Docker involved. Prints the plan, per-iteration test outcomes, and the final
status/diff so the loop is demonstrable end-to-end.

Usage:
    uv run python scripts/run_local_job.py --repo tests/fixtures/toy_repo \\
        --issue "Fix the off-by-one bug in calculate_total()"
"""

import argparse
import asyncio
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402
from src.db import crud  # noqa: E402
from src.db.session import async_session_factory  # noqa: E402
from src.graph.state import AgentState  # noqa: E402
from src.worker.job_runner import run_job  # noqa: E402
from src.logging_config import configure_logging  # noqa: E402


def _copy_repo_to_scratch(repo_path: str) -> str:
    """Work on a throwaway copy so re-running the demo against the toy repo
    doesn't leave it permanently mutated. Uses settings.workspace_dir (under
    the project root), NOT the system temp dir -- Docker Desktop on Mac
    doesn't reliably bind-mount /tmp or /private/var, only /Users paths;
    see docker_manager.py's docstring.
    """
    scratch = Path(settings.workspace_dir).resolve() / f"job-{uuid.uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    dest = scratch / Path(repo_path).name
    shutil.copytree(repo_path, dest)
    return str(dest)


def _print_diff(repo_path: str, original_repo: str) -> None:
    diff = subprocess.run(
        ["diff", "-ru", original_repo, repo_path],
        capture_output=True,
        text=True,
    )
    print("\n--- diff against original ---")
    print(diff.stdout or "(no changes)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent loop against a local repo.")
    parser.add_argument("--repo", required=True, help="Path to the local repo to work on")
    parser.add_argument("--issue", required=True, help="Issue description (title/body combined)")
    parser.add_argument("--max-iterations", type=int, default=6)
    args = parser.parse_args()

    configure_logging()

    original_repo = str(Path(args.repo).resolve())
    working_repo = _copy_repo_to_scratch(original_repo)
    print(f"Working copy: {working_repo}")

    initial_state: AgentState = {
        "job_id": str(uuid.uuid4()),
        "repo_local_path": working_repo,
        "issue_title": args.issue,
        "issue_body": args.issue,
        "repo_full_name": "",
        "issue_number": None,
        "base_branch": "main",
        "work_branch": None,
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

    # run_job owns the whole wrapper: creates the job row, starts the cost
    # budget, enforces the wall-clock timeout, and persists cost + final
    # status. Invoking the graph directly here skipped all of that.
    final_state = asyncio.run(run_job(initial_state, triggered_by="manual:cli"))

    print("\n--- plan ---")
    for step in final_state["plan_steps"]:
        print(f"- [{step['action']}] {step['file']}: {step['description']}")

    print("\n--- iterations ---")
    for i, result in enumerate(final_state["test_history"], start=1):
        outcome = "PASS" if result["passed"] else f"FAIL ({result['failure_signature']})"
        print(f"attempt {i}: {result['command']} -> {outcome}")

    print(f"\n--- final status: {final_state['status']} ---")
    if final_state["debug_analysis"]:
        print(f"last debug analysis:\n{final_state['debug_analysis']}")

    _print_usage(initial_state["job_id"])
    _print_diff(working_repo, original_repo)


def _print_usage(job_id: str) -> None:
    """Reads back what run_job persisted rather than the in-process budget.

    start_job_budget() runs inside run_job's task context, so the ContextVar
    isn't visible here once asyncio.run() returns -- and reading the row has
    the side benefit of showing what actually reached the database.
    """

    async def _fetch():
        async with async_session_factory() as session:
            return await crud.get_job(session, uuid.UUID(job_id))

    try:
        job = asyncio.run(_fetch())
    except Exception as exc:  # noqa: BLE001 -- reporting only
        print(f"\n(could not read usage from db: {exc})")
        return
    if job is None:
        return

    print("\n--- token usage (persisted) ---")
    print(f"  input tokens : {job.total_tokens_input:,}")
    print(f"  output tokens: {job.total_tokens_output:,}")
    print(f"  cost         : ${job.total_cost_usd:.6f} of ${settings.job_cost_budget_usd:.2f} cap")
    print(f"  iterations   : {job.iteration_count}")


if __name__ == "__main__":
    main()
