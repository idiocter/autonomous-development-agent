"""Exercises the real Docker-backed sandbox against the toy repo fixture --
requires Docker Desktop running and the sandbox image built
(`docker build -f docker/Dockerfile.sandbox -t autonomous-dev-agent-sandbox:latest docker/`).
Skipped automatically if the Docker daemon isn't reachable.

Uses a scratch dir under the project root, NOT pytest's default tmp_path
(which resolves under /private/var) -- Docker Desktop on Mac doesn't
reliably bind-mount /tmp or /private/var, only /Users paths. Mounting an
unshared path doesn't error, it silently mounts an empty directory, which
looks exactly like "no tests collected" rather than a mount failure. See
docker_manager.py's docstring.
"""

import shutil
import uuid
from pathlib import Path

import docker
import pytest

from src.sandbox.docker_manager import run_in_sandbox

_SCRATCH_ROOT = Path(__file__).resolve().parent.parent.parent / "workspaces" / "test-scratch"


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker daemon not available")


@pytest.fixture
def toy_repo_copy():
    dest = _SCRATCH_ROOT / f"toy_repo-{uuid.uuid4().hex[:8]}"
    shutil.copytree("tests/fixtures/toy_repo", dest)
    yield str(dest)
    shutil.rmtree(dest, ignore_errors=True)


def test_sandbox_detects_failing_test(toy_repo_copy):
    result = run_in_sandbox(toy_repo_copy, "python -m pytest -q")

    assert result["passed"] is False
    assert result["exit_code"] == 1
    assert "failed" in result["failure_signature"]


def test_sandbox_passes_after_fix(toy_repo_copy):
    from src.tools.filesystem_tools import str_replace

    str_replace(toy_repo_copy, "calculator.py", "range(len(prices) - 1)", "range(len(prices))")

    result = run_in_sandbox(toy_repo_copy, "python -m pytest -q")

    assert result["passed"] is True
    assert result["exit_code"] == 0


def test_sandbox_enforces_timeout(toy_repo_copy):
    result = run_in_sandbox(toy_repo_copy, "sleep 30", timeout_s=2)

    assert result["passed"] is False
    assert "timed out" in result["stderr"]


def test_sandbox_has_no_network_access(toy_repo_copy):
    result = run_in_sandbox(
        toy_repo_copy, "python -c \"import urllib.request; urllib.request.urlopen('http://example.com', timeout=3)\""
    )
    assert result["passed"] is False
