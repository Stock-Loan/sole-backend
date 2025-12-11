from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.settings import settings
from app.db.session import engine
from app.utils.redis_client import get_redis_client

APP_VERSION = "0.1.0"


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
    return {"status": "ok", "version": APP_VERSION}


def _overall_status(checks: dict[str, dict[str, Any]]) -> tuple[str, bool]:
    ready = all(check.get("status") == "ok" for check in checks.values())
    return ("ok" if ready else "degraded", ready)


async def live_payload() -> dict[str, str]:
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def ready_payload() -> dict[str, Any]:
    checks = {
        "api": await _check_api(),
        "database": await _check_db(),
        "redis": await _check_redis(),
    }
    overall, ready = _overall_status(checks)
    return {
        "status": overall,
        "ready": ready,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


async def status_summary_payload() -> dict[str, Any]:
    checks = {
        "api": await _check_api(),
        "database": await _check_db(),
        "redis": await _check_redis(),
    }
    overall, ready = _overall_status(checks)
    return {
        "status": overall,
        "ready": ready,
        "environment": settings.environment,
        "version": APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


async def health_payload() -> dict[str, Any]:
    return await ready_payload()
