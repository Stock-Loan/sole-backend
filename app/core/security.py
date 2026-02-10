from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import uuid
from pathlib import Path
import re
import time
from typing import Any

from pwdlib import PasswordHash
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError

from app.core.settings import settings

logger = logging.getLogger(__name__)


_password_hash = PasswordHash.recommended()

_COMMON_WEAK_PASSWORDS = {
    "password",
    "password123",
    "admin123",
    "qwerty123",
    "changeme",
    "changeme123",
    "welcome123",
    "letmein",
}
_SPECIAL_CHAR_RE = re.compile(r"[^A-Za-z0-9]")


def _validate_password_complexity(password: str) -> None:
    min_len = settings.default_password_min_length
    if len(password) < min_len:
        raise ValueError(f"Password too short; minimum {min_len} characters")
    if not any(char.islower() for char in password):
        raise ValueError("Password must include at least one lowercase letter")
    if not any(char.isupper() for char in password):
        raise ValueError("Password must include at least one uppercase letter")
    if not any(char.isdigit() for char in password):
        raise ValueError("Password must include at least one numeric character")
    if _SPECIAL_CHAR_RE.search(password) is None:
        raise ValueError("Password must include at least one special character")
    if password.strip().lower() in _COMMON_WEAK_PASSWORDS:
        raise ValueError("Password is too common; choose a stronger password")


def get_password_hash(password: str) -> str:
    _validate_password_complexity(password)
    return _password_hash.hash(password)


def hash_password_for_internal_use(password: str) -> str:
    """Hash a password value without complexity policy checks.

    Intended for internal non-user credentials such as timing-equalization
    dummy hashes and migration helpers.
    """
    return _password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _password_hash.verify(plain_password, hashed_password)


class JWTKeyError(RuntimeError):
    pass


@dataclass(slots=True)
class _CachedJWTKey:
    value: str
    loaded_at: float


_private_key_cache: _CachedJWTKey | None = None
_public_key_cache: _CachedJWTKey | None = None


def _key_cache_ttl_seconds() -> int:
    return max(0, settings.jwt_key_cache_ttl_seconds)


def clear_jwt_key_cache() -> None:
    global _private_key_cache, _public_key_cache
    _private_key_cache = None
    _public_key_cache = None


def _is_key_cache_valid(cache: _CachedJWTKey | None) -> bool:
    if cache is None:
        return False
    ttl_seconds = _key_cache_ttl_seconds()
    if ttl_seconds <= 0:
        return False
    return (time.monotonic() - cache.loaded_at) < ttl_seconds


def _load_private_key() -> str:
    global _private_key_cache
    if _is_key_cache_valid(_private_key_cache):
        return _private_key_cache.value

    if settings.jwt_private_key:
        key = settings.jwt_private_key
    elif settings.jwt_private_key_path:
        key = _read_key(settings.jwt_private_key_path)
    else:
        raise JWTKeyError("JWT private key not configured")

    _private_key_cache = _CachedJWTKey(value=key, loaded_at=time.monotonic())
    return key


def _load_public_key() -> str:
    global _public_key_cache
    if _is_key_cache_valid(_public_key_cache):
        return _public_key_cache.value

    if settings.jwt_public_key:
        key = settings.jwt_public_key
    elif settings.jwt_public_key_path:
        key = _read_key(settings.jwt_public_key_path)
    else:
        raise JWTKeyError("JWT public key not configured")

    _public_key_cache = _CachedJWTKey(value=key, loaded_at=time.monotonic())
    return key


def _resolve_key_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        if candidate.exists():
            return candidate
        project_root = Path(__file__).resolve().parents[2]
        # Fallback for host-absolute paths inside containers (e.g. /home/.../secrets/*.pem)
        fallback = project_root / "secrets" / candidate.name
        logger.warning(
            "Configured key path %s does not exist; falling back to %s",
            candidate,
            fallback,
        )
        return fallback.resolve()
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / candidate).resolve()


def _read_key(path: str) -> str:
    resolved_path = _resolve_key_path(path)
    with open(resolved_path, "r", encoding="utf-8") as key_file:
        return key_file.read()


def create_pre_org_token(identity_id: str, *, ttl_minutes: int = 5) -> str:
    """Create a short-lived token for the pre-org-selection lobby phase."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ttl_minutes)
    to_encode: dict[str, Any] = {
        "sub": identity_id,
        "type": "pre_org",
        "aud": "sole-backend",
        "iat": now,
        "exp": expire,
    }
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_pre_org_token(token: str) -> dict[str, Any]:
    """Decode and validate a pre-org token."""
    payload = decode_token(token, expected_type="pre_org")
    identity_id = payload.get("sub")
    if not identity_id:
        raise ValueError("Invalid pre-org token")
    return payload


def create_access_token(
    subject: str,
    *,
    org_id: str,
    identity_id: str,
    is_superuser: bool,
    expires_delta: timedelta | None = None,
    token_version: int | None = None,
    mfa_authenticated: bool = False,
    mfa_method: str | None = None,
    impersonator_user_id: str | None = None,
    impersonator_identity_id: str | None = None,
    impersonation_started_at: datetime | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode: dict[str, Any] = {
        "sub": subject,
        "org": org_id,
        "iid": identity_id,
        "su": bool(is_superuser),
        "mfa": bool(mfa_authenticated),
        "aud": "sole-backend",
        "exp": expire,
        "iat": now,
        "type": "access",
    }
    if token_version is not None:
        to_encode["tv"] = token_version
    if mfa_method:
        to_encode["mfa_method"] = str(mfa_method)
    if impersonator_user_id:
        to_encode["imp"] = impersonator_user_id
    if impersonator_identity_id:
        to_encode["imp_iid"] = impersonator_identity_id
    if impersonation_started_at is not None:
        to_encode["imp_started"] = int(impersonation_started_at.timestamp())
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    subject: str,
    *,
    org_id: str,
    identity_id: str,
    is_superuser: bool,
    expires_delta: timedelta | None = None,
    token_version: int | None = None,
    mfa_authenticated: bool = False,
    mfa_method: str | None = None,
    impersonator_user_id: str | None = None,
    impersonator_identity_id: str | None = None,
    impersonation_started_at: datetime | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.refresh_token_expire_minutes))
    jti = str(uuid.uuid4())
    to_encode: dict[str, Any] = {
        "sub": subject,
        "org": org_id,
        "iid": identity_id,
        "su": bool(is_superuser),
        "mfa": bool(mfa_authenticated),
        "aud": "sole-backend",
        "exp": expire,
        "iat": now,
        "type": "refresh",
        "jti": jti,
    }
    if token_version is not None:
        to_encode["tv"] = token_version
    if mfa_method:
        to_encode["mfa_method"] = str(mfa_method)
    if impersonator_user_id:
        to_encode["imp"] = impersonator_user_id
    if impersonator_identity_id:
        to_encode["imp_iid"] = impersonator_identity_id
    if impersonation_started_at is not None:
        to_encode["imp_started"] = int(impersonation_started_at.timestamp())
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    public_key = _load_public_key()
    try:
        payload = jwt.decode(
            token, public_key, algorithms=[settings.jwt_algorithm], audience="sole-backend"
        )
        if expected_type and payload.get("type") != expected_type:
            raise ValueError(f"Unexpected token type: {payload.get('type')}")
        return payload
    except ExpiredSignatureError as exc:
        raise ValueError("Token expired") from exc
    except InvalidTokenError as exc:  # pragma: no cover - basic placeholder
        raise ValueError("Invalid token") from exc


def create_mfa_challenge_token(identity_id: str, org_id: str, *, ttl_minutes: int = 5) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ttl_minutes)
    to_encode: dict[str, Any] = {
        "sub": identity_id,
        "org": org_id,
        "type": "mfa_challenge",
        "aud": "sole-backend",
        "iat": now,
        "exp": expire,
    }
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_mfa_challenge_token(token: str) -> dict[str, Any]:
    payload = decode_token(token, expected_type="mfa_challenge")
    identity_id = payload.get("sub")
    org_id = payload.get("org")
    if not identity_id or not org_id:
        raise ValueError("Invalid MFA challenge token")
    return payload


def create_mfa_setup_token(identity_id: str, org_id: str, *, ttl_minutes: int = 10) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ttl_minutes)
    to_encode: dict[str, Any] = {
        "sub": identity_id,
        "org": org_id,
        "type": "mfa_setup",
        "aud": "sole-backend",
        "iat": now,
        "exp": expire,
    }
    private_key = _load_private_key()
    return jwt.encode(to_encode, private_key, algorithm=settings.jwt_algorithm)


def decode_mfa_setup_token(token: str) -> dict[str, Any]:
    payload = decode_token(token, expected_type="mfa_setup")
    identity_id = payload.get("sub")
    org_id = payload.get("org")
    if not identity_id or not org_id:
        raise ValueError("Invalid MFA setup token")
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
        "aud": "sole-backend",
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
        "aud": "sole-backend",
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
