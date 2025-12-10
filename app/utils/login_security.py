from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from redis.exceptions import RedisError

from app.core.settings import settings
from app.utils.redis_client import get_redis_client


def _ttl(seconds: int) -> int:
    return max(1, seconds)


async def rate_limit(key: str, limit: int, window_seconds: int) -> None:
    redis = get_redis_client()
    try:
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = await pipe.execute()
        if count > limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    except RedisError:
        return


async def check_lockout(identifier: str) -> None:
    redis = get_redis_client()
    try:
        locked_until = await redis.get(f"lock:{identifier}")
        if locked_until:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts; try later")
    except RedisError:
        return


async def register_login_attempt(identifier: str, success: bool) -> None:
    redis = get_redis_client()
    fail_key = f"fail:{identifier}"
    lock_key = f"lock:{identifier}"
    try:
        if success:
            await redis.delete(fail_key)
            await redis.delete(lock_key)
            return
        attempts = await redis.incr(fail_key)
        await redis.expire(fail_key, _ttl(settings.login_lockout_minutes * 60))
        if attempts >= settings.login_attempt_limit:
            await redis.setex(lock_key, _ttl(settings.login_lockout_minutes * 60), 1)
            await redis.delete(fail_key)
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Account temporarily locked due to failed attempts")
    except RedisError:
        return


async def is_refresh_used(jti: str) -> bool:
    redis = get_redis_client()
    try:
        return bool(await redis.get(f"refresh_used:{jti}"))
    except RedisError:
        return False


async def mark_refresh_used(jti: str, expires_at: datetime) -> None:
    redis = get_redis_client()
    ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    if ttl <= 0:
        return
    try:
        await redis.setex(f"refresh_used:{jti}", ttl, 1)
    except RedisError:
        return
