from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status

from app.core.security import verify_password
from app.core.settings import settings
from app.utils.login_security import check_lockout, rate_limit, register_login_attempt

_FAKE_HASH = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWrn3ILAWO.P3K.fc8G2.0G7u6g.2"


def constant_time_verify(user_password_hash: Optional[str], password: str) -> bool:
    if user_password_hash:
        return verify_password(password, user_password_hash)
    # Dummy verification to equalize timing
    verify_password(password, _FAKE_HASH)
    return False


async def enforce_login_limits(ip: str, email: str) -> None:
    await rate_limit(f"ip:{ip}", limit=settings.rate_limit_per_minute, window_seconds=60)
    await rate_limit(f"email:{email}", limit=settings.rate_limit_per_minute, window_seconds=60)
    await check_lockout(email)


async def record_login_attempt(email: str, success: bool) -> None:
    await register_login_attempt(email, success)
