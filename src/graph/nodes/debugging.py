from src.agents.debugging_agent import call_debugger
from src.graph.state import AgentState


def debugging_node(state: AgentState) -> dict:
    analysis = call_debugger(state)
    return {"debug_analysis": analysis, "status": "coding"}
