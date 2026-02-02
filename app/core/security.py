from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from passlib.context import CryptContext
from jose import JWTError, ExpiredSignatureError, jwt

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


def _resolve_key_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        if candidate.exists():
            return candidate
        project_root = Path(__file__).resolve().parents[2]
        # Fallback for host-absolute paths inside containers (e.g. /home/.../secrets/*.pem)
        fallback = project_root / "secrets" / candidate.name
        return fallback.resolve()
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / candidate).resolve()


def _read_key(path: str) -> str:
    resolved_path = _resolve_key_path(path)
    with open(resolved_path, "r", encoding="utf-8") as key_file:
        return key_file.read()


def create_access_token(
    subject: str,
    *,
    org_id: str,
    is_superuser: bool,
    expires_delta: timedelta | None = None,
    token_version: int | None = None,
    mfa_authenticated: bool = False,
    mfa_method: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode: dict[str, Any] = {
        "sub": subject,
        "org": org_id,
        "su": bool(is_superuser),
        "mfa": bool(mfa_authenticated),
        "exp": expire,
        "iat": now,
        "type": "access",
    }
    if token_version is not None:
        to_encode["tv"] = token_version
    if mfa_method:
        to_encode["mfa_method"] = str(mfa_method)
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    subject: str,
    *,
    org_id: str,
    is_superuser: bool,
    expires_delta: timedelta | None = None,
    token_version: int | None = None,
    mfa_authenticated: bool = False,
    mfa_method: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.refresh_token_expire_minutes))
    jti = str(uuid.uuid4())
    to_encode: dict[str, Any] = {
        "sub": subject,
        "org": org_id,
        "su": bool(is_superuser),
        "mfa": bool(mfa_authenticated),
        "exp": expire,
        "iat": now,
        "type": "refresh",
        "jti": jti,
    }
    if token_version is not None:
        to_encode["tv"] = token_version
    if mfa_method:
        to_encode["mfa_method"] = str(mfa_method)
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    public_key = _load_public_key()
    try:
        payload = jwt.decode(token, public_key, algorithms=[settings.jwt_algorithm])
        if expected_type and payload.get("type") != expected_type:
            raise ValueError(f"Unexpected token type: {payload.get('type')}")
        return payload
    except ExpiredSignatureError as exc:
        raise ValueError("Token expired") from exc
    except JWTError as exc:  # pragma: no cover - basic placeholder
        raise ValueError("Invalid token") from exc


def create_login_challenge_token(email: str, org_id: str, *, ttl_minutes: int = 5) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ttl_minutes)
    to_encode: dict[str, Any] = {
        "sub": email,
        "org": org_id,
        "type": "login_challenge",
        "iat": now,
        "exp": expire,
    }
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def create_mfa_challenge_token(user_id: str, org_id: str, *, ttl_minutes: int = 5) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ttl_minutes)
    to_encode: dict[str, Any] = {
        "sub": user_id,
        "org": org_id,
        "type": "mfa_challenge",
        "iat": now,
        "exp": expire,
    }
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_mfa_challenge_token(token: str) -> dict[str, Any]:
    payload = decode_token(token, expected_type="mfa_challenge")
    user_id = payload.get("sub")
    org_id = payload.get("org")
    if not user_id or not org_id:
        raise ValueError("Invalid MFA challenge token")
    return payload


def create_mfa_setup_token(user_id: str, org_id: str, *, ttl_minutes: int = 10) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ttl_minutes)
    to_encode: dict[str, Any] = {
        "sub": user_id,
        "org": org_id,
        "type": "mfa_setup",
        "iat": now,
        "exp": expire,
    }
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_mfa_setup_token(token: str) -> dict[str, Any]:
    payload = decode_token(token, expected_type="mfa_setup")
    user_id = payload.get("sub")
    org_id = payload.get("org")
    if not user_id or not org_id:
        raise ValueError("Invalid MFA setup token")
    return payload


def decode_login_challenge_token(token: str) -> dict[str, Any]:
    payload = decode_token(token, expected_type="login_challenge")
    email = payload.get("sub")
    org_id = payload.get("org")
    if not email or not org_id:
        raise ValueError("Invalid challenge token")
    return payload


def create_step_up_challenge_token(
    user_id: str,
    org_id: str,
    action: str,
    *,
    ttl_minutes: int = 5,
) -> str:
    """Create a short-lived token for step-up MFA challenge."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ttl_minutes)
    to_encode: dict[str, Any] = {
        "sub": user_id,
        "org": org_id,
        "action": action,
        "type": "step_up_challenge",
        "iat": now,
        "exp": expire,
    }
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_step_up_challenge_token(token: str) -> dict[str, Any]:
    """Decode and validate a step-up MFA challenge token."""
    payload = decode_token(token, expected_type="step_up_challenge")
    user_id = payload.get("sub")
    org_id = payload.get("org")
    action = payload.get("action")
    if not user_id or not org_id or not action:
        raise ValueError("Invalid step-up challenge token")
    return payload


def create_step_up_token(
    user_id: str,
    org_id: str,
    action: str,
    *,
    ttl_minutes: int = 5,
) -> str:
    """Create a short-lived token proving step-up MFA was completed for an action."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ttl_minutes)
    to_encode: dict[str, Any] = {
        "sub": user_id,
        "org": org_id,
        "action": action,
        "type": "step_up",
        "iat": now,
        "exp": expire,
    }
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_step_up_token(token: str) -> dict[str, Any]:
    """Decode and validate a step-up MFA token."""
    payload = decode_token(token, expected_type="step_up")
    user_id = payload.get("sub")
    org_id = payload.get("org")
    action = payload.get("action")
    if not user_id or not org_id or not action:
        raise ValueError("Invalid step-up token")
    return payload
