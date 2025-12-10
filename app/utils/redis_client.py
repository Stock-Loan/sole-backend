from functools import lru_cache

from redis.asyncio import Redis

from app.core.settings import settings


@lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)
