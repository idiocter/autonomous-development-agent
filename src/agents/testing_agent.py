"""Unlike the other agents, Testing doesn't need an LLM call: given a repo,
the test command is either configured or auto-detected, and running it is
deterministic. The Debugging agent is what reasons about failures.
"""

from pathlib import Path

from src.graph.state import AgentState, TestResult
from src.tools.sandbox_tools import run_in_sandbox


def detect_test_command(repo_root: str) -> str:
    root = Path(repo_root)
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or list(root.glob("test_*.py")):
        return "python -m pytest -q"
    if (root / "package.json").exists():
        return "npm test --silent"
    return "python -m pytest -q"


def run_tests(state: AgentState) -> TestResult:
    command = state["test_command"] or detect_test_command(state["repo_local_path"])
    return run_in_sandbox(state["repo_local_path"], command)
