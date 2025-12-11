from fastapi import APIRouter

from app.core.health import (
    health_payload,
    live_payload,
    ready_payload,
    status_summary_payload,
)

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Service liveness check")
async def health_live() -> dict:
    return await live_payload()


@router.get("/health/ready", summary="Service readiness check")
async def health_ready() -> dict:
    return await ready_payload()


@router.get("/health", summary="Backward-compatible readiness check")
async def read_health() -> dict:
    return await health_payload()


@router.get("/status/summary", tags=["status"], summary="Service status summary")
async def status_summary() -> dict:
    return await status_summary_payload()
