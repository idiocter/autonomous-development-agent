"""give_up_reason is what lets the escalation comment tell a human *why* the
run stopped, so these pin both the individual answers and the tie-break order.

The last test is the important one: it asserts the shared predicate and the
routing edge can never disagree. That agreement is the whole reason the two
call sites share a function instead of each re-deriving the conditions.
"""

import pytest

from src.agents import usage
from src.graph.routing import give_up_reason, route_after_test


def _result(passed: bool = False, signature: str | None = "sig-a"):
    return {
        "command": "pytest",
        "exit_code": 0 if passed else 1,
        "passed": passed,
        "stdout": "",
        "stderr": "",
        "failure_signature": None if passed else signature,
    }


def _state(**overrides):
    base = {
        "test_result": _result(),
        "iteration_count": 1,
        "max_iterations": 6,
        "test_history": [],
    }
    base.update(overrides)
    return base


def _repeating(signature: str = "sig-a"):
    return [_result(signature=signature), _result(signature=signature)]


@pytest.fixture(autouse=True)
def _no_budget():
    """Most cases want the budget guard silent; the budget tests opt in."""
    usage._current_budget.set(None)
    yield
    usage._current_budget.set(None)


def _exhaust_budget():
    usage.start_job_budget("job-give-up-reason", budget_usd=0.001)
    usage.record_usage("claude-opus-5", input_tokens=100_000, output_tokens=100_000)


def test_no_reason_while_the_run_can_still_continue():
    assert give_up_reason(_state()) is None


def test_iteration_cap():
    assert give_up_reason(_state(iteration_count=6, max_iterations=6)) == "iteration_cap"


def test_repeating_failure():
    state = _state(iteration_count=2, test_history=_repeating())
    assert give_up_reason(state) == "repeating_failure"


def test_over_budget():
    _exhaust_budget()
    assert give_up_reason(_state()) == "over_budget"


def test_changing_failure_signatures_are_not_a_repeat():
    history = [_result(signature="sig-a"), _result(signature="sig-b")]
    assert give_up_reason(_state(iteration_count=2, test_history=history)) is None


def test_budget_wins_when_the_cap_is_also_reached():
    """Running out of money is the more actionable thing to tell a human:
    raising MAX_ITERATIONS does nothing if the budget is what stopped it."""
    _exhaust_budget()
    state = _state(iteration_count=6, max_iterations=6, test_history=_repeating())
    assert give_up_reason(state) == "over_budget"


def test_repeating_failure_wins_over_the_cap():
    """"It's stuck on one error" tells the human more than "out of attempts",
    and implies a different next step -- more attempts won't help."""
    state = _state(iteration_count=6, max_iterations=6, test_history=_repeating())
    assert give_up_reason(state) == "repeating_failure"


@pytest.mark.parametrize(
    "state",
    [
        _state(),
        _state(iteration_count=6, max_iterations=6),
        _state(iteration_count=2, test_history=_repeating()),
        _state(iteration_count=6, max_iterations=6, test_history=_repeating()),
        _state(test_history=[_result(signature="sig-a"), _result(signature="sig-b")]),
    ],
)
def test_route_after_test_agrees_with_give_up_reason(state):
    """The anti-drift test. If these two ever disagree, the escalation comment
    starts explaining a decision the graph didn't actually make."""
    assert (route_after_test(state) == "give_up") == (give_up_reason(state) is not None)


def test_passing_tests_route_pass_regardless_of_any_guard():
    """A green suite short-circuits before the guards are consulted -- otherwise
    a run that passed on its last attempt would be reported as a give-up."""
    _exhaust_budget()
    state = _state(
        test_result=_result(passed=True),
        iteration_count=6,
        max_iterations=6,
        test_history=_repeating(),
    )
    assert route_after_test(state) == "pass"
