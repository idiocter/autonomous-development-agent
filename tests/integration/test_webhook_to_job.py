"""Verifies the webhook -> job enqueue path end-to-end using a real,
correctly-signed payload against FastAPI's TestClient -- no real GitHub
webhook delivery needed. The actual clone/issue-fetch is monkeypatched out
of enqueue_job's downstream worker loop by never letting the worker run
(these tests only assert the handler's own behavior: signature
verification, label gating, dedup, and that a valid request reaches
enqueue_job with the right JobRequest).
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers import webhooks
from src.config import settings

WEBHOOK_SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _set_webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", WEBHOOK_SECRET)
    webhooks._seen_deliveries.clear()
    yield
    webhooks._seen_deliveries.clear()


@pytest.fixture
def client():
    # The FastAPI lifespan starts the real worker loop, which would try to
    # process anything we enqueue -- irrelevant to what these tests assert,
    # and undesirable since it'd attempt a real GitHub clone. Use the
    # app directly without triggering lifespan by not using `with TestClient`
    # as a context manager for startup/shutdown events here.
    return TestClient(app)


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _labeled_payload(*, labels=("agent:work-on-it",), action="labeled") -> dict:
    return {
        "action": action,
        "repository": {"full_name": "acme/widgets", "default_branch": "main"},
        "issue": {"number": 42, "labels": [{"name": name} for name in labels]},
    }


def test_webhook_rejects_missing_signature(client):
    body = json.dumps(_labeled_payload()).encode()
    response = client.post(
        "/webhooks/github", content=body, headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d1"}
    )
    assert response.status_code == 401


def test_webhook_rejects_invalid_signature(client):
    body = json.dumps(_labeled_payload()).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d2",
        },
    )
    assert response.status_code == 401


def test_webhook_enqueues_on_valid_labeled_event(client, monkeypatch):
    captured = {}

    async def fake_enqueue(request):
        captured["request"] = request

    monkeypatch.setattr(webhooks, "enqueue_job", fake_enqueue)

    body = json.dumps(_labeled_payload()).encode()
    signature = _sign(WEBHOOK_SECRET, body)
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d3",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert captured["request"].repo_full_name == "acme/widgets"
    assert captured["request"].issue_number == 42
    assert captured["request"].triggered_by == "webhook:issue_labeled"


def test_webhook_ignores_event_without_trigger_label(client, monkeypatch):
    monkeypatch.setattr(webhooks, "enqueue_job", lambda r: pytest.fail("should not enqueue"))

    body = json.dumps(_labeled_payload(labels=("bug",))).encode()
    signature = _sign(WEBHOOK_SECRET, body)
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": signature, "X-GitHub-Event": "issues", "X-GitHub-Delivery": "d4"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_ignores_non_issues_events(client, monkeypatch):
    monkeypatch.setattr(webhooks, "enqueue_job", lambda r: pytest.fail("should not enqueue"))

    body = json.dumps(_labeled_payload()).encode()
    signature = _sign(WEBHOOK_SECRET, body)
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": signature, "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d5"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_dedupes_repeated_delivery_id(client, monkeypatch):
    calls = []

    async def fake_enqueue(request):
        calls.append(request)

    monkeypatch.setattr(webhooks, "enqueue_job", fake_enqueue)

    body = json.dumps(_labeled_payload()).encode()
    signature = _sign(WEBHOOK_SECRET, body)
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": "issues", "X-GitHub-Delivery": "d6"}

    first = client.post("/webhooks/github", content=body, headers=headers)
    second = client.post("/webhooks/github", content=body, headers=headers)

    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "ignored"
    assert len(calls) == 1
