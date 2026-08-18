from langgraph.graph import END, StateGraph

from src.graph.nodes.coding import coding_node
from src.graph.nodes.debugging import debugging_node
from src.graph.nodes.escalation import human_escalation_node
from src.graph.nodes.planner import planner_node
from src.graph.nodes.pr_creation import pr_node
from src.graph.nodes.testing import testing_node
from src.graph.routing import route_after_test
from src.graph.state import AgentState


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("coding", coding_node)
    graph.add_node("testing", testing_node)
    graph.add_node("debugging", debugging_node)
    graph.add_node("pr_creation", pr_node)
    graph.add_node("human_escalation", human_escalation_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "coding")
    graph.add_edge("coding", "testing")
    graph.add_conditional_edges(
        "testing",
        route_after_test,
        {"pass": "pr_creation", "retry": "debugging", "give_up": "human_escalation"},
    )
    graph.add_edge("debugging", "coding")
    graph.add_edge("pr_creation", END)
    graph.add_edge("human_escalation", END)

    return graph.compile()
