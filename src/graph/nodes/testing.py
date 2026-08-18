from src.agents.testing_agent import run_tests
from src.graph.state import AgentState


def testing_node(state: AgentState) -> dict:
    result = run_tests(state)
    return {
        "test_result": result,
        "test_history": state["test_history"] + [result],
        "iteration_count": state["iteration_count"] + 1,
        "status": "pr_creation" if result["passed"] else "debugging",
    }
