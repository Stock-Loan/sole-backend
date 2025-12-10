from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from functools import lru_cache
from typing import Any, Optional

from passlib.context import CryptContext
from jose import JWTError, jwt

from app.core.settings import settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    min_len = settings.default_password_min_length
    if len(password) < min_len:
        raise ValueError(f"Password too short; minimum {min_len} characters")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


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


def create_access_token(
    subject: str, expires_delta: timedelta | None = None, token_version: int | None = None
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode: dict[str, Any] = {"sub": subject, "exp": expire, "type": "access"}
    if token_version is not None:
        to_encode["tv"] = token_version
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    subject: str,
    expires_delta: timedelta | None = None,
    token_version: int | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.refresh_token_expire_minutes)
    )
    jti = str(uuid.uuid4())
    to_encode: dict[str, Any] = {"sub": subject, "exp": expire, "type": "refresh", "jti": jti}
    if token_version is not None:
        to_encode["tv"] = token_version
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    public_key = _load_public_key()
    try:
        payload = jwt.decode(token, public_key, algorithms=[settings.jwt_algorithm])
        if expected_type and payload.get("type") != expected_type:
            raise ValueError(f"Unexpected token type: {payload.get('type')}")
        return payload
    except JWTError as exc:  # pragma: no cover - basic placeholder
        raise ValueError("Invalid token") from exc
