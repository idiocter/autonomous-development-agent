from typing import cast

from src.agents.planner_agent import call_planner, scan_untrusted_inputs
from src.graph.state import AgentState
from src.tools.rag_tools import rag_retrieve


def planner_node(state: AgentState) -> dict:
    query = f"{state['issue_title']}\n{state['issue_body']}"
    repo_url = state["repo_full_name"] or state["repo_local_path"]
    relevant_context = rag_retrieve(state["repo_local_path"], query, k=8, repo_url=repo_url)
    planner_state = cast(AgentState, {**state, "relevant_context": relevant_context})
    # Scanned here rather than inside call_planner so the result lands in graph
    # state and survives to the PR node -- a warning nobody reviewing the PR can
    # see is worth very little.
    injection_findings = scan_untrusted_inputs(planner_state)
    plan_steps = call_planner(planner_state)
    return {
        "relevant_context": relevant_context,
        "plan_steps": plan_steps,
        "injection_findings": injection_findings,
        "status": "coding",
    }
