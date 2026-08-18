"""Typed CRUD helpers shared by the worker (job_runner.py) and the API
(routers/jobs.py) so both go through the same persistence logic instead of
each hand-rolling queries.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Event, Job, Task

TERMINAL_STATUSES = {"done", "failed", "needs_human", "cancelled"}


async def create_job(
    session: AsyncSession,
    *,
    repo_url: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    triggered_by: str,
    max_iterations: int,
    job_id: uuid.UUID | None = None,
) -> Job:
    job = Job(
        id=job_id or uuid.uuid4(),
        repo_url=repo_url,
        issue_number=issue_number,
        issue_title=issue_title,
        issue_body=issue_body,
        triggered_by=triggered_by,
        max_iterations=max_iterations,
        status="queued",
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id)


async def list_jobs(session: AsyncSession, limit: int = 50) -> list[Job]:
    result = await session.execute(select(Job).order_by(Job.created_at.desc()).limit(limit))
    return list(result.scalars().all())


async def update_job_status(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    status: str,
    pr_url: str | None = None,
    pr_number: int | None = None,
    iteration_count: int | None = None,
    work_branch: str | None = None,
) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.status = status
    if pr_url is not None:
        job.pr_url = pr_url
    if pr_number is not None:
        job.pr_number = pr_number
    if iteration_count is not None:
        job.iteration_count = iteration_count
    if work_branch is not None:
        job.work_branch = work_branch
    if status in TERMINAL_STATUSES:
        job.completed_at = datetime.now(UTC)
    await session.commit()


async def add_job_cost(
    session: AsyncSession, job_id: uuid.UUID, *, tokens_input: int, tokens_output: int, cost_usd: float
) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.total_tokens_input += tokens_input
    job.total_tokens_output += tokens_output
    job.total_cost_usd += cost_usd
    await session.commit()


async def create_task(
    session: AsyncSession, *, job_id: uuid.UUID, node_name: str, iteration: int, input_summary: dict
) -> Task:
    task = Task(
        job_id=job_id, node_name=node_name, iteration=iteration, status="running", input_summary=input_summary
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def complete_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    status: str,
    output_summary: dict,
    llm_model: str | None = None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    task = await session.get(Task, task_id)
    if task is None:
        return
    task.status = status
    task.output_summary = output_summary
    task.llm_model = llm_model
    task.tokens_input = tokens_input
    task.tokens_output = tokens_output
    task.cost_usd = cost_usd
    task.duration_ms = duration_ms
    task.error = error
    task.completed_at = datetime.now(UTC)
    await session.commit()


async def log_event(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    event_type: str,
    payload: dict,
    task_id: uuid.UUID | None = None,
) -> None:
    session.add(Event(job_id=job_id, task_id=task_id, event_type=event_type, payload=payload))
    await session.commit()
