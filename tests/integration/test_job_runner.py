"""Exercises job_runner.run_job's wall-clock timeout and Postgres
persistence against the real Postgres container (`docker-compose up -d
postgres`), with a mocked graph.invoke since there's no live
ANTHROPIC_API_KEY in this environment yet to run the real LangGraph nodes
end-to-end.
"""

import time
import uuid

from src.db import crud
from src.db.session import async_session_factory
from src.graph.state import AgentState
from src.worker import job_runner


def _initial_state(job_id: str, **overrides) -> AgentState:
    base: AgentState = {
        "job_id": job_id,
        "repo_local_path": "/tmp/fake",
        "issue_title": "test issue",
        "issue_body": "test body",
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
        "max_iterations": 6,
        "status": "planning",
        "error_log": [],
        "pr_url": None,
    }
    base.update(overrides)
    return base


async def test_run_job_persists_success(monkeypatch):
    job_id = str(uuid.uuid4())

    class FakeGraph:
        def invoke(self, state, config=None):
            return {**state, "status": "done", "pr_url": None, "iteration_count": 1}

    monkeypatch.setattr(job_runner, "build_graph", lambda: FakeGraph())

    final_state = await job_runner.run_job(_initial_state(job_id), timeout_s=5)

    assert final_state["status"] == "done"

    async with async_session_factory() as session:
        db_job = await crud.get_job(session, uuid.UUID(job_id))
        assert db_job is not None
        assert db_job.status == "done"
        assert db_job.completed_at is not None


async def test_run_job_times_out_and_marks_failed(monkeypatch):
    job_id = str(uuid.uuid4())

    class HangingGraph:
        def invoke(self, state, config=None):
            time.sleep(5)
            return {**state, "status": "done"}

    monkeypatch.setattr(job_runner, "build_graph", lambda: HangingGraph())

    final_state = await job_runner.run_job(_initial_state(job_id), timeout_s=1)

    assert final_state["status"] == "failed"
    assert any("timeout" in e for e in final_state["error_log"])

    async with async_session_factory() as session:
        db_job = await crud.get_job(session, uuid.UUID(job_id))
        assert db_job.status == "failed"


async def test_run_job_persists_cost_from_usage_tracker(monkeypatch):
    from src.agents.usage import record_usage

    job_id = str(uuid.uuid4())

    class SpendingGraph:
        def invoke(self, state, config=None):
            record_usage("claude-opus-5", input_tokens=10_000, output_tokens=5_000)
            return {**state, "status": "done", "iteration_count": 1}

    monkeypatch.setattr(job_runner, "build_graph", lambda: SpendingGraph())

    await job_runner.run_job(_initial_state(job_id), timeout_s=5)

    async with async_session_factory() as session:
        db_job = await crud.get_job(session, uuid.UUID(job_id))
        assert db_job.total_cost_usd > 0
        assert db_job.total_tokens_input == 10_000
        assert db_job.total_tokens_output == 5_000


async def test_run_job_populates_tasks_and_events(monkeypatch):
    """The gap this closes: tasks and events were schema-only. A real run
    through the instrumented graph must now leave an audit trail and give
    the SSE feed something to stream."""
    from sqlalchemy import select

    from src.db.models import Event, Task
    from src.graph.build_graph import _instrumented

    job_id = str(uuid.uuid4())

    class InstrumentedGraph:
        """Two real nodes, wrapped exactly as build_graph wraps them."""

        def invoke(self, state, config=None):
            plan = _instrumented("planner", lambda s: {"status": "coding", "plan_steps": [1, 2]})
            test = _instrumented(
                "testing",
                lambda s: {"status": "done", "test_result": {"passed": True, "failure_signature": None}},
            )
            state = {**state, **plan(state)}
            return {**state, **test(state)}

    monkeypatch.setattr(job_runner, "build_graph", lambda: InstrumentedGraph())

    await job_runner.run_job(_initial_state(job_id), timeout_s=30)

    async with async_session_factory() as session:
        tasks = (
            (await session.execute(select(Task).where(Task.job_id == uuid.UUID(job_id))))
            .scalars().all()
        )
        events = (
            (await session.execute(select(Event).where(Event.job_id == uuid.UUID(job_id))))
            .scalars().all()
        )

    assert {t.node_name for t in tasks} == {"planner", "testing"}
    assert all(t.status == "succeeded" for t in tasks)
    assert all(t.completed_at is not None and t.duration_ms is not None for t in tasks)

    by_node = {t.node_name: t for t in tasks}
    assert by_node["planner"].output_summary["plan_step_count"] == 2
    assert by_node["testing"].output_summary["test_passed"] is True

    kinds = {e.event_type for e in events}
    assert "node_started" in kinds and "node_completed" in kinds
    assert len(events) == 4  # started + completed for each of the two nodes
