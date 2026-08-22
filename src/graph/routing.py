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
from src.graph.state import AgentState, GiveUpReason, TestResult
from src.tools.github_tools import has_substantive_change

RouteAfterTest = Literal["pass", "retry", "give_up"]


def give_up_reason(state: AgentState) -> GiveUpReason | None:
    """Which guard would stop this run, or None if it should carry on.

    Covers both halves of the decision. When the tests fail it's the familiar
    question of whether to retry; when they *pass* there is still one reason
    not to ship, which is that nothing was actually changed.

    Single source of truth for the give-up predicate. It's called from two
    places: the conditional edge below, which only needs the yes/no, and
    human_escalation_node, which needs to tell the human *which* guard tripped.
    Deriving it twice independently would work today and drift tomorrow.

    Order matters when several conditions hold at once -- hitting the iteration
    cap on a repeating failure is common -- and the first match is what gets
    reported. Money first: it's the hardest constraint and the one that
    truncates work mid-thought. Then "stuck on one error", which tells the
    human more than "out of attempts". The cap is the residual case.

    Safe to call from a node: is_over_budget() reads a ContextVar that
    asyncio.to_thread copied into the graph's worker thread (see
    job_runner.py), and no model call happens between this edge and the
    escalation node, so the answer can't change underneath us.
    """
    result = state["test_result"]

    if result is not None and result["passed"]:
        # Green tests mean "the suite still passes", not "the issue is fixed" --
        # the suite describes the behaviour that existed before the run, so it
        # keeps passing when the agent does nothing. Shipping that as a success
        # is worse than giving up, because a green PR doesn't get re-read.
        workspace = state.get("repo_local_path")
        base = state.get("base_branch")
        if workspace and base and not has_substantive_change(workspace, base):
            return "no_change"
        return None

    if is_over_budget():
        return "over_budget"
    if _is_repeating_failure(state["test_history"]):
        return "repeating_failure"
    if state["iteration_count"] >= state["max_iterations"]:
        return "iteration_cap"
    return None


def route_after_test(state: AgentState) -> RouteAfterTest:
    result = state["test_result"]
    assert result is not None, "route_after_test called before testing_node ran"

    if give_up_reason(state) is not None:
        return "give_up"
    return "pass" if result["passed"] else "retry"


def _is_repeating_failure(history: list[TestResult]) -> bool:
    if len(history) < 2:
        return False
    # A None signature means the run passed, which routes "pass" and ends the
    # graph -- so two of them can't actually accumulate here. Guarding anyway:
    # this predicate shouldn't quietly become "two passes in a row" if that
    # ever stops being true.
    last = history[-1]["failure_signature"]
    return last is not None and last == history[-2]["failure_signature"]
