from src.agents.coding_agent import call_coder
from src.graph.state import AgentState


def coding_node(state: AgentState) -> dict:
    summary = call_coder(state)
    return {
        "commit_message": summary,
        "status": "testing",
        "debug_analysis": None,  # consumed by this coding pass
    }
