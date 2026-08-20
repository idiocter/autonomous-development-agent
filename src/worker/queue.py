"""In-process asyncio.Queue consumed by a single background worker task --
sufficient for local, single-process operation. Celery/Redis is the
documented production-scaling upgrade (see plan.md), not required here.

Holds lightweight JobRequest objects, NOT a fully-prepared AgentState:
cloning the repo and fetching issue context are I/O-bound "job prep" work
that belongs in the worker loop, not the webhook handler -- the handler
must ACK well under GitHub's ~10s webhook timeout, so it only validates and
enqueues.
"""

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import structlog

from src.config import settings
from src.github_integration.auth import get_auth_provider
from src.graph.state import AgentState
from src.tools.github_tools import (
    clone_repo,
    create_work_branch,
    get_client,
    get_issue_context,
    get_repo,
)
from src.worker.job_runner import run_job

logger = structlog.get_logger(__name__)


@dataclass
class JobRequest:
    repo_full_name: str
    issue_number: int
    base_branch: str
    triggered_by: str


_queue: asyncio.Queue[JobRequest] = asyncio.Queue()
_worker_task: asyncio.Task | None = None


async def enqueue_job(request: JobRequest) -> None:
    await _queue.put(request)


def _prepare_initial_state(request: JobRequest) -> AgentState:
    token = get_auth_provider().get_token()
    client = get_client()
    gh_repo = get_repo(client, request.repo_full_name)
    issue_ctx = get_issue_context(gh_repo, request.issue_number)

    job_id = str(uuid.uuid4())
    scratch = Path(settings.workspace_dir).resolve() / f"job-{job_id[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)
    local_path = scratch / request.repo_full_name.split("/")[-1]
    local_repo = clone_repo(request.repo_full_name, str(local_path), token)

    work_branch = f"agent/issue-{request.issue_number}-{job_id[:8]}"
    create_work_branch(local_repo, request.base_branch, work_branch)

    return {
        "job_id": job_id,
        "repo_local_path": str(local_path),
        "issue_title": issue_ctx.title,
        "issue_body": issue_ctx.body,
        "repo_full_name": request.repo_full_name,
        "issue_number": request.issue_number,
        "base_branch": request.base_branch,
        "work_branch": work_branch,
        "plan_steps": [],
        "relevant_context": [],
        "injection_findings": {},
        "file_diffs": [],
        "commit_message": None,
        "test_command": "",
        "test_result": None,
        "test_history": [],
        "debug_analysis": None,
        "iteration_count": 0,
        "max_iterations": settings.max_iterations,
        "status": "planning",
        "error_log": [],
        "pr_url": None,
    }


async def _worker_loop() -> None:
    while True:
        request = await _queue.get()
        try:
            initial_state = await asyncio.to_thread(_prepare_initial_state, request)
            await run_job(initial_state, triggered_by=request.triggered_by)
        except Exception:
            # A single job's uncaught failure (bad clone, GitHub API error,
            # etc.) must not kill the worker loop for every subsequent job.
            logger.exception(
                "job failed unexpectedly",
                repo=request.repo_full_name,
                issue=request.issue_number,
            )
        finally:
            _queue.task_done()


def start_worker() -> asyncio.Task:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop())
    return _worker_task


def queue_size() -> int:
    return _queue.qsize()
