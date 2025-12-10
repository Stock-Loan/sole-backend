from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.settings import settings
from app.db.session import engine
from app.utils.redis_client import get_redis_client


async def _check_db() -> dict[str, str]:
    try:
        async with engine.begin() as conn:  # type: AsyncConnection
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # pragma: no cover - exercised in runtime
        return {"status": "error", "error": str(exc)}


async def _check_redis() -> dict[str, str]:
    try:
        redis = get_redis_client()
        await redis.ping()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


async def _check_api() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


async def health_payload() -> dict[str, dict[str, str] | str]:
    db_status = await _check_db()
    redis_status = await _check_redis()
    api_status = await _check_api()

    overall = "ok"
    if (
        db_status.get("status") != "ok"
        or redis_status.get("status") != "ok"
        or api_status.get("status") != "ok"
    ):
        overall = "degraded"

    return {
        "status": overall,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "api": api_status,
            "database": db_status,
            "redis": redis_status,
        },
    }
