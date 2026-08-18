from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/readiness")
async def readiness() -> dict:
    return {"status": "ready"}
