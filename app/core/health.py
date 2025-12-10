from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.settings import settings
from app.db.session import engine


async def _check_db() -> dict[str, str]:
    try:
        async with engine.begin() as conn:  # type: AsyncConnection
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # pragma: no cover - exercised in runtime
        return {"status": "error", "error": str(exc)}


async def _check_redis() -> dict[str, str]:
    # Placeholder: implemented once redis client is added
    return {"status": "unknown", "error": "redis check not implemented"}


async def health_payload() -> dict[str, dict[str, str] | str]:
    db_status = await _check_db()
    redis_status = await _check_redis()

    overall = "ok" if db_status.get("status") == "ok" else "degraded"

    return {
        "status": overall,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "database": db_status,
            "redis": redis_status,
        },
    }
