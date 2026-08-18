"""Conditional-edge routing functions.

Four independent loop-safety guards, none of which alone is trusted to
catch every runaway case:
  1. hard iteration cap
  2. repeated-failure-signature detection
  3. cumulative cost budget (src/agents/usage.py) -- catches a single
     expensive call blowing past budget faster than the iteration cap would
  4. wall-clock job timeout -- enforced by the worker around the whole
     graph invocation (src/worker/job_runner.py), since a hung call inside
     a single node wouldn't otherwise trip any of the above
"""

from typing import Literal

from src.agents.usage import is_over_budget
from src.graph.state import AgentState, TestResult

RouteAfterTest = Literal["pass", "retry", "give_up"]


def route_after_test(state: AgentState) -> RouteAfterTest:
    result = state["test_result"]
    assert result is not None, "route_after_test called before testing_node ran"

    if result["passed"]:
        return "pass"
    if state["iteration_count"] >= state["max_iterations"]:
        return "give_up"
    if is_over_budget():
        return "give_up"
    if _is_repeating_failure(state["test_history"]):
        return "give_up"
    return "retry"


def _is_repeating_failure(history: list[TestResult]) -> bool:
    if len(history) < 2:
        return False
    return history[-1]["failure_signature"] == history[-2]["failure_signature"]
