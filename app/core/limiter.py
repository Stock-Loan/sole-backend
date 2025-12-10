from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.settings import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
    storage_uri=settings.redis_url,
)

__all__ = ["limiter"]
