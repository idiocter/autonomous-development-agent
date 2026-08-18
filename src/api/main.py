"""FastAPI app: webhook ingress + jobs REST API + health. Never does agent
work inline in the request/response cycle -- see src/worker/queue.py.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.routers import health, jobs, webhooks
from src.logging_config import configure_logging
from src.worker.queue import start_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    start_worker()
    yield


app = FastAPI(title="autonomous-dev-agent", lifespan=lifespan)
app.include_router(webhooks.router)
app.include_router(jobs.router)
app.include_router(health.router)
