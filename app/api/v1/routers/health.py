from fastapi import APIRouter

from app.core.health import health_payload

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Service health check")
async def read_health() -> dict:
    return await health_payload()
