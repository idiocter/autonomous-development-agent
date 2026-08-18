"""Exercises GET /jobs and GET /jobs/{id} against the real Postgres
container via FastAPI's TestClient + the real crud layer (no mocking of the
DB -- only the webhook's downstream GitHub calls are ever mocked, not this).
"""

import uuid

from fastapi.testclient import TestClient

from src.api.main import app
from src.db import crud
from src.db.session import async_session_factory

client = TestClient(app)


async def test_get_job_returns_404_for_unknown_id():
    response = client.get(f"/jobs/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_get_job_returns_created_job():
    job_id = uuid.uuid4()
    async with async_session_factory() as session:
        await crud.create_job(
            session,
            job_id=job_id,
            repo_url="acme/widgets",
            issue_number=7,
            issue_title="Add feature X",
            issue_body="body",
            triggered_by="manual:cli",
            max_iterations=6,
        )

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(job_id)
    assert body["repo_url"] == "acme/widgets"
    assert body["issue_number"] == 7
    assert body["status"] == "queued"


async def test_list_jobs_includes_recent_job():
    job_id = uuid.uuid4()
    async with async_session_factory() as session:
        await crud.create_job(
            session,
            job_id=job_id,
            repo_url="acme/list-test",
            issue_number=1,
            issue_title="t",
            issue_body="b",
            triggered_by="manual:cli",
            max_iterations=6,
        )

    response = client.get("/jobs")

    assert response.status_code == 200
    ids = [j["id"] for j in response.json()["jobs"]]
    assert str(job_id) in ids


async def test_stream_job_events_yields_events_then_terminates():
    job_id = uuid.uuid4()
    async with async_session_factory() as session:
        await crud.create_job(
            session,
            job_id=job_id,
            repo_url="acme/sse-test",
            issue_number=1,
            issue_title="t",
            issue_body="b",
            triggered_by="manual:cli",
            max_iterations=6,
        )
        await crud.log_event(session, job_id=job_id, event_type="status_change", payload={"to": "planning"})
        # Job is already terminal by the time the stream opens -- the poll
        # loop should emit the backlog then an "event: done" and return,
        # rather than hang on its poll interval.
        await crud.update_job_status(session, job_id, status="done")

    lines = []
    with client.stream("GET", f"/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            lines.append(line)
            if line.startswith("event: done"):
                break

    assert any("status_change" in line for line in lines)
    assert any(line.startswith("event: done") for line in lines)


async def test_stream_job_events_returns_error_for_unknown_job():
    with client.stream("GET", f"/jobs/{uuid.uuid4()}/events") as response:
        lines = list(response.iter_lines())

    assert any("job not found" in line for line in lines)


def test_cancel_job_returns_not_implemented():
    response = client.post(f"/jobs/{uuid.uuid4()}/cancel")
    assert response.status_code == 501


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
