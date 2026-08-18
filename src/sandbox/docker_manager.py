"""Real Docker-backed command execution, replacing the Phase 1 subprocess
stub. Same `run_in_sandbox(repo_root, command, timeout_s) -> TestResult`
shape as the stub it replaces (src/tools/sandbox_tools.py), so callers
didn't need to change.

Isolation model: code editing happens on the host filesystem via the coding
agent's read_file/write_file tools; only *execution* happens here, inside an
isolated, resource-limited, no-network-by-default container that always
gets torn down in a finally block.

Mac Docker Desktop gotcha: `repo_root` MUST be a path under a directory
Docker Desktop actually bind-mounts. On this platform /tmp and
/private/var (tempfile.mkdtemp()'s default) are NOT shared by default --
the container silently sees an empty directory instead of erroring, which
looks exactly like "no tests found" rather than a mount failure. Only
/Users paths are reliably shared. `settings.workspace_dir` (default:
"workspaces" under the project root) exists specifically to keep job
workspaces off the system temp dir for this reason.
"""

import docker
from docker.errors import NotFound

from src.config import settings
from src.graph.state import TestResult
from src.sandbox.resource_limits import DEFAULT_LIMITS

_client: docker.DockerClient | None = None


def get_docker_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    head, tail = text[: limit // 2], text[-limit // 2 :]
    return f"{head}\n...[truncated]...\n{tail}"


def _failure_signature(output: str) -> str:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "unknown_failure"


def run_in_sandbox(repo_root: str, command: str, timeout_s: int | None = None) -> TestResult:
    timeout_s = timeout_s or settings.sandbox_timeout_seconds
    client = get_docker_client()

    container = client.containers.run(
        settings.sandbox_image,
        command=["sh", "-c", command],
        volumes={repo_root: {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        detach=True,
        **DEFAULT_LIMITS,
    )

    timed_out = False
    exit_code = -1
    stdout, stderr = "", ""
    try:
        try:
            wait_result = container.wait(timeout=timeout_s)
            exit_code = wait_result.get("StatusCode", -1)
        except Exception:  # noqa: BLE001 -- a hung container must not hang the whole job
            timed_out = True
            try:
                container.kill()
            except Exception:  # noqa: BLE001 -- already exited/gone is fine, removal below still runs
                pass

        if timed_out:
            stderr = f"command timed out after {timeout_s}s"
        else:
            # docker-py's logs() doesn't reliably demux stdout/stderr on this
            # call path across versions -- combined output is sufficient
            # since callers only need the last line for a failure signature.
            stdout = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
    finally:
        # Must run even if wait()/logs() raised something other than the
        # timeout case above -- a leaked container on every unexpected error
        # is exactly the failure mode this guards against.
        try:
            container.remove(force=True)
        except NotFound:
            pass

    passed = exit_code == 0 and not timed_out
    signature = None if passed else _failure_signature(stderr or stdout)

    return TestResult(
        command=command,
        exit_code=exit_code,
        passed=passed,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        failure_signature=signature,
    )


def build_sandbox_image(dockerfile_dir: str = "docker", tag: str | None = None) -> str:
    tag = tag or settings.sandbox_image
    client = get_docker_client()
    client.images.build(path=dockerfile_dir, dockerfile="Dockerfile.sandbox", tag=tag, rm=True)
    return tag
