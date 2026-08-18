from typing import Literal, TypedDict


class PlanStep(TypedDict):
    file: str
    action: Literal["create", "edit", "delete"]
    description: str


class RetrievedChunk(TypedDict):
    file_path: str
    start_line: int
    end_line: int
    content: str


class FileDiff(TypedDict):
    file: str
    diff: str


class TestResult(TypedDict):
    command: str
    exit_code: int
    passed: bool
    stdout: str
    stderr: str
    failure_signature: str | None


JobStatus = Literal[
    "planning",
    "coding",
    "testing",
    "debugging",
    "pr_creation",
    "done",
    "failed",
    "needs_human",
]


class AgentState(TypedDict):
    # Immutable job context
    job_id: str
    repo_local_path: str
    issue_title: str
    issue_body: str

    # GitHub context (Phase 2+; empty strings/None for local-only Phase 1 runs)
    repo_full_name: str  # "owner/repo", empty for local-only runs
    issue_number: int | None
    base_branch: str
    work_branch: str | None

    # Planner output
    plan_steps: list[PlanStep]
    relevant_context: list[RetrievedChunk]

    # Coding output
    file_diffs: list[FileDiff]
    commit_message: str | None

    # Testing output
    test_command: str
    test_result: TestResult | None
    test_history: list[TestResult]

    # Debugging output
    debug_analysis: str | None

    # Control flow
    iteration_count: int
    max_iterations: int
    status: JobStatus
    error_log: list[str]

    # Outputs
    pr_url: str | None
