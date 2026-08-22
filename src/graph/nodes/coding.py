from src.agents.coding_agent import call_coder
from src.graph.state import AgentState


def coding_node(state: AgentState) -> dict:
    summary = call_coder(state)
    # Deliberately does NOT clear debug_analysis. Clearing it here was a no-op
    # for the coder -- this node's only predecessors are planner (where it's
    # already None) and debugging (which just set it) -- but it did destroy the
    # analysis before the escalation comment could show it, which is the single
    # most useful thing a human picking up a failed run can read.
    #
    # If an edge into coding is ever added that doesn't pass through debugging,
    # a stale analysis would leak into the coder's prompt; keep a separate
    # last_debug_analysis field at that point rather than restoring the reset.
    return {"commit_message": summary, "status": "testing"}
