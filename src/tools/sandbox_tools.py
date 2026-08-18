"""`run_in_sandbox` now delegates to the real Docker-backed executor
(src/sandbox/docker_manager.py) built in Phase 3. `run_in_sandbox_subprocess`
is the original Phase 1 stub, kept around for tests/tooling that shouldn't
need a built Docker image just to exercise unrelated logic.
"""

import subprocess

from src.graph.state import TestResult


def run_in_sandbox(repo_root: str, command: str, timeout_s: int | None = None) -> TestResult:
    from src.sandbox.docker_manager import run_in_sandbox as docker_run_in_sandbox

    return docker_run_in_sandbox(repo_root, command, timeout_s)


def run_in_sandbox_subprocess(repo_root: str, command: str, timeout_s: int = 60) -> TestResult:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        exit_code = -1
        stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr = f"command timed out after {timeout_s}s"

    passed = exit_code == 0
    signature = None if passed else _failure_signature(stderr or stdout)

    return TestResult(
        command=command,
        exit_code=exit_code,
        passed=passed,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        failure_signature=signature,
    )


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    head, tail = text[: limit // 2], text[-limit // 2 :]
    return f"{head}\n...[truncated]...\n{tail}"


def _failure_signature(output: str) -> str:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "unknown_failure"
