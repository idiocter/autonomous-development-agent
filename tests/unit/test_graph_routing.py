from src.agents import usage
from src.graph.routing import route_after_test


def _state(**overrides):
    base = {
        "test_result": {"command": "pytest", "exit_code": 1, "passed": False, "stdout": "", "stderr": "", "failure_signature": "sig-a"},
        "iteration_count": 1,
        "max_iterations": 6,
        "test_history": [],
    }
    base.update(overrides)
    return base


def test_route_pass_when_tests_pass():
    state = _state(test_result={"command": "pytest", "exit_code": 0, "passed": True, "stdout": "", "stderr": "", "failure_signature": None})
    assert route_after_test(state) == "pass"


def test_route_retry_below_caps():
    usage._current_budget.set(None)
    state = _state(iteration_count=1, max_iterations=6)
    assert route_after_test(state) == "retry"


def test_route_give_up_at_iteration_cap():
    usage._current_budget.set(None)
    state = _state(iteration_count=6, max_iterations=6)
    assert route_after_test(state) == "give_up"


def test_route_give_up_on_repeated_failure_signature():
    usage._current_budget.set(None)
    history = [
        {"command": "pytest", "exit_code": 1, "passed": False, "stdout": "", "stderr": "", "failure_signature": "sig-a"},
        {"command": "pytest", "exit_code": 1, "passed": False, "stdout": "", "stderr": "", "failure_signature": "sig-a"},
    ]
    state = _state(iteration_count=2, max_iterations=6, test_history=history)
    assert route_after_test(state) == "give_up"


def test_route_retry_on_different_failure_signatures():
    usage._current_budget.set(None)
    history = [
        {"command": "pytest", "exit_code": 1, "passed": False, "stdout": "", "stderr": "", "failure_signature": "sig-a"},
        {"command": "pytest", "exit_code": 1, "passed": False, "stdout": "", "stderr": "", "failure_signature": "sig-b"},
    ]
    state = _state(iteration_count=2, max_iterations=6, test_history=history)
    assert route_after_test(state) == "retry"


def test_route_give_up_when_over_cost_budget():
    usage.start_job_budget("job-routing-test", budget_usd=0.001)
    usage.record_usage("claude-opus-5", input_tokens=100_000, output_tokens=100_000)

    state = _state(iteration_count=1, max_iterations=6)
    assert route_after_test(state) == "give_up"

    usage._current_budget.set(None)
