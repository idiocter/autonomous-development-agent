"""Tests for the per-node instrumentation in build_graph.

The important properties are the failure modes: instrumentation is
observability, so it must never change what the agent does -- not swallow a
node's exception, not alter its return value, and not break the run when the
database is unreachable.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from src.graph import build_graph as bg


@pytest.fixture
def captured(monkeypatch):
    """Replace the two persistence bridges with recorders."""
    calls = {"start": [], "end": []}

    def fake_start(job_id, node_name, state):
        calls["start"].append({"job_id": job_id, "node": node_name})
        return uuid.uuid4()

    def fake_end(job_id, task_id, node_name, **kw):
        calls["end"].append({"job_id": job_id, "node": node_name, **kw})

    monkeypatch.setattr(bg, "_record_start", fake_start)
    monkeypatch.setattr(bg, "_record_end", fake_end)
    return calls


def test_wrapper_returns_the_node_result_unchanged(captured):
    node = MagicMock(return_value={"status": "coding", "plan_steps": [1, 2]})
    wrapped = bg._instrumented("planner", node)

    out = wrapped({"job_id": str(uuid.uuid4()), "iteration_count": 0})

    assert out == {"status": "coding", "plan_steps": [1, 2]}
    node.assert_called_once()


def test_wrapper_records_start_and_success(captured):
    wrapped = bg._instrumented("planner", lambda s: {"status": "coding"})
    wrapped({"job_id": str(uuid.uuid4()), "iteration_count": 0})

    assert captured["start"][0]["node"] == "planner"
    assert captured["end"][0]["status"] == "succeeded"
    assert captured["end"][0]["summary"]["status"] == "coding"
    assert captured["end"][0]["duration_ms"] >= 0


def test_wrapper_records_failure_and_reraises(captured):
    def boom(_state):
        raise RuntimeError("node exploded")

    wrapped = bg._instrumented("coding", boom)

    with pytest.raises(RuntimeError, match="node exploded"):
        wrapped({"job_id": str(uuid.uuid4()), "iteration_count": 1})

    assert captured["end"][0]["status"] == "failed"
    assert "node exploded" in captured["end"][0]["error"]


def test_db_failure_does_not_break_the_run(monkeypatch):
    """The whole point: Postgres down must not stop the agent."""
    def explode(*_a, **_k):
        raise ConnectionError("postgres is down")

    monkeypatch.setattr(bg.crud, "create_task", explode)
    monkeypatch.setattr(bg.crud, "complete_task", explode)
    monkeypatch.setattr(bg.crud, "log_event", explode)

    wrapped = bg._instrumented("planner", lambda s: {"status": "coding"})
    out = wrapped({"job_id": str(uuid.uuid4()), "iteration_count": 0})

    assert out == {"status": "coding"}  # node result survives intact


def test_no_job_id_skips_persistence_entirely(monkeypatch):
    """Guards the FK: tasks.job_id references jobs.id, so a graph invoked
    without a job row must not attempt an insert."""
    called = []
    monkeypatch.setattr(bg, "_safe", lambda *a, **k: called.append(1))

    assert bg._record_start(None, "planner", {}) is None
    bg._record_end(None, None, "planner", status="succeeded", summary={}, duration_ms=1, error=None)
    assert called == []


# --- summary shaping -----------------------------------------------------

def test_summary_captures_useful_fields():
    s = bg._summarize({
        "status": "testing",
        "plan_steps": [1, 2, 3],
        "iteration_count": 2,
        "pr_url": "https://github.com/o/r/pull/1",
        "error_log": ["a", "b"],
        "test_result": {"passed": False, "failure_signature": "AssertionError: x"},
    })
    assert s["status"] == "testing"
    assert s["plan_step_count"] == 3
    assert s["iteration_count"] == 2
    assert s["pr_url"].endswith("/pull/1")
    assert s["error_count"] == 2
    assert s["test_passed"] is False
    assert "AssertionError" in s["failure_signature"]


def test_summary_never_carries_bulk_payloads():
    """Diffs, file contents and messages must not reach the DB."""
    s = bg._summarize({
        "status": "coding",
        "file_diffs": ["-" * 50_000],
        "relevant_context": [{"content": "x" * 50_000}],
        "messages": ["y" * 50_000],
    })
    assert set(s) == {"status"}
    assert len(str(s)) < 200


def test_summary_truncates_long_failure_signatures():
    s = bg._summarize({"test_result": {"passed": False, "failure_signature": "E" * 5000}})
    assert len(s["failure_signature"]) == 200


def test_summary_tolerates_non_dict_updates():
    assert bg._summarize(None) == {}
    assert bg._summarize("not a dict") == {}


# --- wiring --------------------------------------------------------------

def test_every_node_is_instrumented():
    """A node added without instrumentation would silently stop being
    recorded, so assert the wrapper is applied to all of them."""
    graph = MagicMock()
    added = {}
    graph.add_node.side_effect = lambda name, fn: added.__setitem__(name, fn)

    import src.graph.build_graph as mod
    original = mod.StateGraph
    mod.StateGraph = MagicMock(return_value=graph)
    try:
        mod.build_graph()
    finally:
        mod.StateGraph = original

    expected = {"planner", "coding", "testing", "debugging", "pr_creation", "human_escalation"}
    assert set(added) == expected
    for name, fn in added.items():
        assert fn.__name__ == f"{name}_instrumented", f"{name} is not instrumented"
