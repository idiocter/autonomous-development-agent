from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repo_url: str
    issue_number: int
    issue_title: str
    status: str
    work_branch: str | None
    pr_url: str | None
    iteration_count: int
    max_iterations: int
    total_cost_usd: float
    created_at: datetime
    completed_at: datetime | None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]


class WebhookAckResponse(BaseModel):
    status: str
    detail: str | None = None
