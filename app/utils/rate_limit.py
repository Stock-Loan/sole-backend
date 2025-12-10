from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict

from fastapi import HTTPException, status

from app.core.settings import settings

_attempts: Dict[str, Deque[datetime]] = defaultdict(deque)
_lockouts: Dict[str, datetime] = {}


def _prune(key: str, window: timedelta, now: datetime) -> None:
    dq = _attempts[key]
    while dq and now - dq[0] > window:
        dq.popleft()


def check_login_lockout(identifier: str) -> None:
    now = datetime.now(timezone.utc)
    locked_until = _lockouts.get(identifier)
    if locked_until and locked_until > now:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts; try later")
    if locked_until and locked_until <= now:
        _lockouts.pop(identifier, None)


def register_login_attempt(identifier: str, success: bool) -> None:
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=settings.login_lockout_minutes)
    _prune(identifier, window, now)
    dq = _attempts[identifier]
    if success:
        dq.clear()
        _lockouts.pop(identifier, None)
        return
    dq.append(now)
    if len(dq) >= settings.login_attempt_limit:
        _lockouts[identifier] = now + timedelta(minutes=settings.login_lockout_minutes)
        dq.clear()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Account temporarily locked due to failed attempts")
