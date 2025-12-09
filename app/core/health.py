from datetime import datetime, timezone

from app.core.settings import settings


def health_payload() -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
