"""The coding node used to clear debug_analysis on its way out, which blanked
the escalation comment's most useful section. These pin the fix and the
invariant that makes it safe.
"""

from src.graph.nodes import coding as coding_module
from src.graph.nodes.coding import coding_node


def _state(**overrides):
    base = {
        "debug_analysis": "price arrives as a str from load_catalog()",
        "issue_title": "discount is wrong",
        "issue_body": "",
        "plan_steps": [],
        "relevant_context": [],
        "repo_local_path": "/tmp/repo",
    }
    base.update(overrides)
    return base


def test_debug_analysis_survives_a_coding_pass(monkeypatch):
    """The whole point: it has to still be there when escalation reads it."""
    monkeypatch.setattr(coding_module, "call_coder", lambda state: "did the thing")

    update = coding_node(_state())

    assert "debug_analysis" not in update, "coding must not overwrite the analysis"


def test_coding_node_reports_its_summary_and_next_status(monkeypatch):
    monkeypatch.setattr(coding_module, "call_coder", lambda state: "did the thing")

    update = coding_node(_state())

    assert update == {"commit_message": "did the thing", "status": "testing"}


def test_coding_is_only_reachable_from_planner_and_debugging():
    """The invariant behind not clearing the analysis. If a new edge into
    coding appears, this fails and the comment in coding.py explains what to
    do about it -- rather than a stale analysis quietly reaching the coder."""
    import inspect

    from src.graph import build_graph

    source = inspect.getsource(build_graph.build_graph)
    into_coding = {
        line.split('"')[1]
        for line in source.splitlines()
        if 'add_edge(' in line and '"coding")' in line
    }
    assert into_coding == {"planner", "debugging"}
