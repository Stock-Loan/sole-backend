from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from jose import JWTError, jwt

from app.core.settings import settings


class JWTKeyError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load_private_key() -> str:
    if settings.jwt_private_key:
        return settings.jwt_private_key
    if settings.jwt_private_key_path:
        return _read_key(settings.jwt_private_key_path)
    raise JWTKeyError("JWT private key not configured")


@lru_cache(maxsize=1)
def _load_public_key() -> str:
    if settings.jwt_public_key:
        return settings.jwt_public_key
    if settings.jwt_public_key_path:
        return _read_key(settings.jwt_public_key_path)
    raise JWTKeyError("JWT public key not configured")


def _read_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as key_file:
        return key_file.read()


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode: dict[str, Any] = {"sub": subject, "exp": expire}
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    public_key = _load_public_key()
    try:
        return jwt.decode(token, public_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:  # pragma: no cover - basic placeholder
        raise ValueError("Invalid token") from exc
