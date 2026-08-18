"""GET /jobs, GET /jobs/{id}, POST /jobs/{id}/cancel|retry.

Cancel/retry return 501 rather than silently no-op-ing: real cancellation
needs cooperative checks inside long-running node/tool calls, which is a
bigger change than this phase's scope -- see plan.md's Phase 6 stretch list.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.api.schemas import JobListResponse, JobResponse
from src.db import crud
from src.db.models import Event
from src.db.session import async_session_factory

router = APIRouter(prefix="/jobs", tags=["jobs"])

_SSE_POLL_INTERVAL_S = 1.0


@router.get("", response_model=JobListResponse)
async def list_jobs(limit: int = 50, session: AsyncSession = Depends(get_db)) -> JobListResponse:
    jobs = await crud.list_jobs(session, limit=limit)
    return JobListResponse(jobs=[JobResponse.model_validate(j) for j in jobs])


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_db)) -> JobResponse:
    job = await crud.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobResponse.model_validate(job)


@router.get("/{job_id}/events")
async def stream_job_events(job_id: uuid.UUID) -> StreamingResponse:
    """Polling-based SSE: no pub/sub infra needed for this project's scale
    (a handful of concurrent jobs, not a high-throughput API). Opens a
    fresh short-lived session per poll rather than holding one open for the
    whole (potentially minutes-long) stream lifetime.
    """

    async def _events() -> AsyncGenerator[str, None]:
        last_seen_created_at = None
        while True:
            async with async_session_factory() as session:
                job = await crud.get_job(session, job_id)
                if job is None:
                    yield "event: error\ndata: job not found\n\n"
                    return

                query = select(Event).where(Event.job_id == job_id).order_by(Event.created_at)
                if last_seen_created_at is not None:
                    query = query.where(Event.created_at > last_seen_created_at)
                result = await session.execute(query)
                new_events = list(result.scalars().all())

            for event in new_events:
                payload = {
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                last_seen_created_at = event.created_at

            if job.status in crud.TERMINAL_STATUSES:
                yield f"event: done\ndata: {job.status}\n\n"
                return

            await asyncio.sleep(_SSE_POLL_INTERVAL_S)

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: uuid.UUID) -> dict:
    raise HTTPException(status_code=501, detail="cancellation not yet implemented")


@router.post("/{job_id}/retry")
async def retry_job(job_id: uuid.UUID) -> dict:
    raise HTTPException(status_code=501, detail="retry not yet implemented")
