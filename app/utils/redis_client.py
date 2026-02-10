from functools import lru_cache

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

from app.core.settings import settings


def redis_key(*parts: object) -> str:
    prefix = (settings.redis_key_prefix or "").strip(":")
    normalized_parts = [str(part).strip(":") for part in parts if str(part).strip(":")]
    if not normalized_parts:
        return prefix
    body = ":".join(normalized_parts)
    return f"{prefix}:{body}" if prefix else body


def redis_pattern(*parts: object) -> str:
    return redis_key(*parts)


@lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    retry = Retry(ExponentialBackoff(), retries=3)
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
        retry=retry,
    )
