"""Graph wiring, plus the per-node instrumentation that feeds the `tasks`
audit trail and the `events` stream behind /jobs/{id}/events.

Instrumentation lives here rather than inside each node because every node is
registered through the same `add_node` call below -- one choke point, so a new
node can't be added and silently skip being recorded.

All persistence is best-effort: a database problem degrades observability, it
must never take down an agent run that is otherwise working.
"""

import asyncio
import time
import uuid
from typing import Any, Callable

import structlog
from langgraph.graph import END, StateGraph

from src.db import crud
from src.db.session import async_session_factory
from src.graph.nodes.coding import coding_node
from src.graph.nodes.debugging import debugging_node
from src.graph.nodes.escalation import human_escalation_node
from src.graph.nodes.planner import planner_node
from src.graph.nodes.pr_creation import pr_node
from src.graph.nodes.testing import testing_node
from src.graph.routing import route_after_test
from src.graph.state import AgentState

logger = structlog.get_logger(__name__)


def _safe(coro_factory: Callable[[], Any], message: str) -> Any:
    """Run an async persistence call from a sync node, swallowing failures.

    asyncio.run() is safe here: graph.invoke() is always executed off the
    event loop -- either a plain script's main thread or job_runner's
    asyncio.to_thread worker -- so no loop is running in *this* thread.
    """
    try:
        return asyncio.run(coro_factory())
    except Exception as exc:  # noqa: BLE001 -- observability must not break the run
        logger.warning(message, error=str(exc))
        return None


def _summarize(update: Any) -> dict:
    """Compact, safe summary of a node's state update.

    Counts and short flags only -- never file contents, diffs or LLM
    messages, which would bloat the row and risk persisting secrets.
    """
    if not isinstance(update, dict):
        return {}
    summary: dict[str, Any] = {}
    if "status" in update:
        summary["status"] = update["status"]
    if "plan_steps" in update:
        summary["plan_step_count"] = len(update["plan_steps"] or [])
    if "iteration_count" in update:
        summary["iteration_count"] = update["iteration_count"]
    if update.get("pr_url"):
        summary["pr_url"] = update["pr_url"]
    if update.get("error_log"):
        summary["error_count"] = len(update["error_log"])
    result = update.get("test_result")
    if isinstance(result, dict):
        summary["test_passed"] = result.get("passed")
        if result.get("failure_signature"):
            summary["failure_signature"] = str(result["failure_signature"])[:200]
    return summary


def _record_start(job_id: str | None, node_name: str, state: AgentState) -> uuid.UUID | None:
    if not job_id:
        return None

    async def _run():
        async with async_session_factory() as session:
            task = await crud.create_task(
                session,
                job_id=uuid.UUID(job_id),
                node_name=node_name,
                iteration=state.get("iteration_count", 0),
                input_summary={"status": state.get("status")},
            )
            await crud.log_event(
                session,
                job_id=uuid.UUID(job_id),
                task_id=task.id,
                event_type="node_started",
                payload={"node": node_name, "iteration": state.get("iteration_count", 0)},
            )
            return task.id

    return _safe(_run, f"could not record start of node {node_name}")


def _record_end(
    job_id: str | None,
    task_id: uuid.UUID | None,
    node_name: str,
    *,
    status: str,
    summary: dict,
    duration_ms: int,
    error: str | None,
) -> None:
    if not job_id:
        return

    async def _run():
        async with async_session_factory() as session:
            if task_id is not None:
                await crud.complete_task(
                    session,
                    task_id,
                    status=status,
                    output_summary=summary,
                    duration_ms=duration_ms,
                    error=error,
                )
            await crud.log_event(
                session,
                job_id=uuid.UUID(job_id),
                task_id=task_id,
                event_type="node_completed" if status == "succeeded" else "node_failed",
                payload={"node": node_name, "duration_ms": duration_ms, **summary},
            )

    _safe(_run, f"could not record completion of node {node_name}")


def _instrumented(node_name: str, fn: Callable[[AgentState], dict]) -> Callable[[AgentState], dict]:
    def wrapper(state: AgentState) -> dict:
        job_id = state.get("job_id")
        task_id = _record_start(job_id, node_name, state)
        started = time.monotonic()

        try:
            update = fn(state)
        except Exception as exc:
            _record_end(
                job_id,
                task_id,
                node_name,
                status="failed",
                summary={},
                duration_ms=int((time.monotonic() - started) * 1000),
                error=str(exc)[:2000],
            )
            raise  # the graph's own error handling still owns the failure

        _record_end(
            job_id,
            task_id,
            node_name,
            status="succeeded",
            summary=_summarize(update),
            duration_ms=int((time.monotonic() - started) * 1000),
            error=None,
        )
        return update

    wrapper.__name__ = f"{node_name}_instrumented"
    return wrapper


def build_graph():
    graph = StateGraph(AgentState)

    for name, fn in (
        ("planner", planner_node),
        ("coding", coding_node),
        ("testing", testing_node),
        ("debugging", debugging_node),
        ("pr_creation", pr_node),
        ("human_escalation", human_escalation_node),
    ):
        graph.add_node(name, _instrumented(name, fn))

    graph.set_entry_point("planner")
    graph.add_edge("planner", "coding")
    graph.add_edge("coding", "testing")
    graph.add_conditional_edges(
        "testing",
        route_after_test,
        {"pass": "pr_creation", "retry": "debugging", "give_up": "human_escalation"},
    )
    graph.add_edge("debugging", "coding")
    graph.add_edge("pr_creation", END)
    graph.add_edge("human_escalation", END)

    return graph.compile()
