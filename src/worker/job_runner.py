"""Wraps a single job's graph invocation with the worker-level loop-safety
guard that can't live inside the graph itself: a wall-clock timeout around
the whole run. (Guards #1-3 -- iteration cap, repeated-failure detection,
cost budget -- live in src/graph/routing.py and fire *inside* the graph;
this is guard #4.) Also owns Postgres persistence of the job's lifecycle and
final cost, correlating the in-memory AgentState.job_id with the Job row's
primary key 1:1 so both refer to the same job.

Python caveat: a timed-out graph.invoke() keeps running in its background
thread even after run_job() returns -- Python threads aren't forcibly
killable. The job is correctly marked failed/timed-out in Postgres and the
caller isn't blocked, but the orphaned thread only stops on its own accord
(e.g. when a tool call it's mid-way through finally returns). This is a
known limitation of thread-based timeouts, not a bug; a process-per-job
model would be needed to truly kill a hung run, which is out of scope here.

Context-propagation gotcha: graph.invoke() runs in a worker thread via
asyncio.to_thread(), NOT loop.run_in_executor() directly -- run_in_executor
does not copy the calling context, so the job's ContextVar-based cost budget
(src/agents/usage.py, set here via start_job_budget) would be invisible to
every node function running in that thread, silently disabling loop-safety
guard #3. asyncio.to_thread copies the context before dispatching, which is
exactly what's needed here.
"""

import asyncio
import uuid

import structlog

from src.agents.usage import get_job_budget, start_job_budget
from src.config import settings
from src.db import crud
from src.db.session import async_session_factory
from src.graph.build_graph import build_graph
from src.graph.state import AgentState

logger = structlog.get_logger(__name__)


async def _best_effort(coro_factory, message: str) -> None:
    """Run a persistence step, downgrading failure to a warning."""
    try:
        await coro_factory()
    except Exception as exc:  # noqa: BLE001 -- observability must not break the run
        logger.warning(message, error=str(exc))


async def run_job(
    initial_state: AgentState, *, timeout_s: int | None = None, triggered_by: str = "manual:cli"
) -> AgentState:
    timeout_s = timeout_s or settings.job_timeout_seconds
    job_uuid = uuid.UUID(initial_state["job_id"])

    # Persistence is observability -- a database outage must not stop the
    # agent from doing its actual job. Routing the CLIs through run_job
    # otherwise made Postgres a hard dependency for runs that previously
    # needed no database at all.
    async def _create():
        async with async_session_factory() as session:
            await crud.create_job(
                session,
                job_id=job_uuid,
                repo_url=initial_state["repo_full_name"] or initial_state["repo_local_path"],
                issue_number=initial_state["issue_number"] or 0,
                issue_title=initial_state["issue_title"],
                issue_body=initial_state["issue_body"],
                triggered_by=triggered_by,
                max_iterations=initial_state["max_iterations"],
            )

    await _best_effort(_create, "could not create job row")

    start_job_budget(initial_state["job_id"], settings.job_cost_budget_usd)
    graph = build_graph()

    def _invoke() -> AgentState:
        return graph.invoke(initial_state, config={"recursion_limit": 50})

    try:
        final_state: AgentState = await asyncio.wait_for(
            asyncio.to_thread(_invoke), timeout=timeout_s
        )
        status = final_state["status"]
    except TimeoutError:
        final_state = {
            **initial_state,
            "status": "failed",
            "error_log": [
                *initial_state["error_log"],
                f"job exceeded wall-clock timeout of {timeout_s}s",
            ],
        }
        status = "failed"

    budget = get_job_budget()

    async def _persist():
        async with async_session_factory() as session:
            if budget is not None:
                await crud.add_job_cost(
                    session,
                    job_uuid,
                    tokens_input=budget.total_input_tokens,
                    tokens_output=budget.total_output_tokens,
                    cost_usd=budget.total_cost_usd,
                )
            await crud.update_job_status(
                session,
                job_uuid,
                status=status,
                pr_url=final_state.get("pr_url"),
                iteration_count=final_state.get("iteration_count", 0),
                work_branch=final_state.get("work_branch"),
            )

    await _best_effort(_persist, "could not persist final job status")

    return final_state
