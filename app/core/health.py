from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from app.core.settings import settings
from app.db.session import engine
from app.utils.redis_client import get_redis_client

APP_VERSION = "0.1.0"

logger = logging.getLogger(__name__)


async def _check_db() -> dict[str, str]:
    try:
        async with engine.begin() as conn:  # type: AsyncConnection
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except SQLAlchemyError as exc:  # pragma: no cover - exercised in runtime
        logger.error("Health check: database error: %s", exc)
        return {"status": "error"}


async def _check_redis() -> dict[str, str]:
    try:
        redis = get_redis_client()
        await redis.ping()
        return {"status": "ok"}
    except RedisError as exc:
        logger.error("Health check: redis error: %s", exc)
        return {"status": "error"}


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
    payload = {
        "status": overall,
        "ready": ready,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if settings.health_include_details:
        payload["environment"] = settings.environment
        payload["checks"] = checks
    return payload


async def status_summary_payload() -> dict[str, Any]:
    checks = {
        "api": await _check_api(),
        "database": await _check_db(),
        "redis": await _check_redis(),
    }
    overall, ready = _overall_status(checks)
    payload = {
        "status": overall,
        "ready": ready,
        "version": APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if settings.health_include_details:
        payload["environment"] = settings.environment
        payload["checks"] = checks
    return payload


async def health_payload() -> dict[str, Any]:
    return await ready_payload()
