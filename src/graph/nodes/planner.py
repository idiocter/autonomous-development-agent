from src.agents.planner_agent import call_planner
from src.graph.state import AgentState
from src.tools.rag_tools import rag_retrieve


def planner_node(state: AgentState) -> dict:
    query = f"{state['issue_title']}\n{state['issue_body']}"
    repo_url = state["repo_full_name"] or state["repo_local_path"]
    relevant_context = rag_retrieve(state["repo_local_path"], query, k=8, repo_url=repo_url)
    plan_steps = call_planner({**state, "relevant_context": relevant_context})
    return {
        "relevant_context": relevant_context,
        "plan_steps": plan_steps,
        "status": "coding",
    }
