"""POST /webhooks/github -- verifies X-Hub-Signature-256 against
GITHUB_WEBHOOK_SECRET before trusting anything in the payload; an
unverified webhook endpoint is an open door to trigger arbitrary,
arbitrary-spend agent runs. Only fires on `issues` events carrying the
`agent:work-on-it` label -- a human always opts a specific issue in, the
agent never autonomously grabs every new issue in the repo.

Handler stays fast (signature check, JSON parse, label check, enqueue) --
the actual clone + issue fetch happens in the worker loop
(src/worker/queue.py), not here, since GitHub expects an ACK well under its
~10s webhook timeout.
"""

import hashlib
import hmac

from fastapi import APIRouter, Header, HTTPException, Request

from src.api.schemas import WebhookAckResponse
from src.config import settings
from src.worker.queue import JobRequest, enqueue_job

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

TRIGGER_LABEL = "agent:work-on-it"

# In-process dedup on GitHub's X-GitHub-Delivery header -- GitHub retries
# webhook deliveries on transient failures, so a retry must not
# double-trigger a job. Fine for a single-worker local deployment; a
# Postgres-backed "is there already a non-terminal job for this issue"
# check is the multi-instance-safe version, a Phase 6 hardening item.
_seen_deliveries: set[str] = set()


def _verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    if not settings.github_webhook_secret:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.github_webhook_secret.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


@router.post("/github", response_model=WebhookAckResponse)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
) -> WebhookAckResponse:
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    if x_github_delivery and x_github_delivery in _seen_deliveries:
        return WebhookAckResponse(status="ignored", detail="duplicate delivery")
    if x_github_delivery:
        _seen_deliveries.add(x_github_delivery)

    if x_github_event != "issues":
        return WebhookAckResponse(status="ignored", detail=f"unhandled event type: {x_github_event}")

    payload = await request.json()
    action = payload.get("action")
    labels = [lbl["name"] for lbl in payload.get("issue", {}).get("labels", [])]

    if action != "labeled" or TRIGGER_LABEL not in labels:
        return WebhookAckResponse(status="ignored", detail="no trigger label present")

    job_request = JobRequest(
        repo_full_name=payload["repository"]["full_name"],
        issue_number=payload["issue"]["number"],
        base_branch=payload["repository"]["default_branch"],
        triggered_by="webhook:issue_labeled",
    )
    await enqueue_job(job_request)

    return WebhookAckResponse(status="queued")
