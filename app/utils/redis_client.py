from functools import lru_cache

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

from app.core.settings import settings


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
