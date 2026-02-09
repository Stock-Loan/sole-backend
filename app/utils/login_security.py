from datetime import datetime, timezone
import logging

from fastapi import HTTPException, status
from redis.exceptions import RedisError

from app.core.settings import settings
from app.utils.redis_client import get_redis_client

logger = logging.getLogger(__name__)


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
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded"
            )
    except RedisError as e:
        logger.error(f"Redis error in rate_limit: {e}")
        # Fail open for general rate limiting to avoid outage, OR fail closed for strict security.
        # For login security, fail closed is usually preferred.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )


async def check_lockout(identifier: str) -> None:
    redis = get_redis_client()
    try:
        locked_until = await redis.get(f"lock:{identifier}")
        if locked_until:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts; try later",
            )
    except RedisError as e:
        logger.error(f"Redis error in check_lockout: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )


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
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Account temporarily locked due to failed attempts",
            )
    except RedisError as e:
        logger.error(f"Redis error in register_login_attempt: {e}")
        # We cannot safely track attempts, so we must fail to prevent brute force
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )


async def mark_refresh_used_atomic(jti: str, expires_at: datetime) -> bool:
    """Atomically mark a refresh token JTI as used.

    Returns True if this is the first use (SET NX succeeded).
    Returns False if the token was already marked (replay detected).
    """
    redis = get_redis_client()
    ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    if ttl <= 0:
        return False  # already expired, treat as replay
    try:
        result = await redis.set(f"refresh_used:{jti}", 1, ex=ttl, nx=True)
        return bool(result)
    except RedisError as e:
        logger.error(f"Redis error in mark_refresh_used_atomic: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )


async def enforce_mfa_rate_limit(mfa_token: str) -> None:
    """Rate-limit MFA code verification attempts per mfa_token.

    Limits to 5 attempts per token to prevent brute-forcing the 6-digit TOTP code
    within the 5-minute token validity window.
    """
    redis = get_redis_client()
    key = f"mfa_attempt:{mfa_token}"
    limit = 5  # Max attempts per MFA token
    window_seconds = 300  # 5 minutes (matches token validity)
    try:
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        count, _ = await pipe.execute()
        if count > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many MFA verification attempts; please request a new code",
            )
    except RedisError as e:
        logger.error(f"Redis error in enforce_mfa_rate_limit: {e}")
        # Fail closed for MFA security
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )


async def check_pbgc_refresh_cooldown(org_id: str) -> None:
    """Enforce a 24-hour cooldown on PBGC rate refresh per organization.

    Raises HTTP 429 if a refresh was already performed within the last 24 hours.
    """
    redis = get_redis_client()
    key = f"pbgc_refresh:{org_id}"
    try:
        if await redis.exists(key):
            ttl = await redis.ttl(key)
            hours_remaining = max(1, ttl // 3600)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"PBGC rates already refreshed recently. Please wait {hours_remaining} hour(s) before refreshing again.",
            )
    except RedisError as e:
        logger.error(f"Redis error in check_pbgc_refresh_cooldown: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )


async def record_pbgc_refresh(org_id: str) -> None:
    """Record a PBGC refresh to enforce the 24-hour cooldown."""
    redis = get_redis_client()
    key = f"pbgc_refresh:{org_id}"
    cooldown_seconds = 24 * 60 * 60  # 24 hours
    try:
        await redis.setex(key, cooldown_seconds, 1)
    except RedisError as e:
        logger.error(f"Redis error in record_pbgc_refresh: {e}")
        # Non-critical, proceed without recording
