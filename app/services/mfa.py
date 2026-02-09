from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.user_mfa_device import UserMfaDevice
from app.models.user_mfa_recovery_code import UserMfaRecoveryCode

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 8  # 8-character codes like "A1B2-C3D4"


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def build_totp_uri(secret: str, email: str, issuer: str) -> str:
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def _hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_device_token() -> str:
    return secrets.token_urlsafe(32)


async def create_remember_device(
    db: AsyncSession,
    *,
    org_id: str,
    user_id,
    expires_at: datetime,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> str:
    raw_token = generate_device_token()
    token_hash = _hash_device_token(raw_token)
    device = UserMfaDevice(
        org_id=org_id,
        user_id=user_id,
        token_hash=token_hash,
        user_agent=user_agent,
        ip_address=ip_address,
        expires_at=expires_at,
        last_used_at=datetime.now(timezone.utc),
    )
    db.add(device)
    await db.flush()
    return raw_token


async def find_valid_device(
    db: AsyncSession,
    *,
    org_id: str,
    user_id,
    remember_token: str,
) -> UserMfaDevice | None:
    token_hash = _hash_device_token(remember_token)
    stmt = select(UserMfaDevice).where(
        UserMfaDevice.org_id == org_id,
        UserMfaDevice.user_id == user_id,
        UserMfaDevice.token_hash == token_hash,
    )
    result = await db.execute(stmt)
    device = result.scalar_one_or_none()
    if not device:
        return None
    now = datetime.now(timezone.utc)
    if device.expires_at <= now:
        return None
    device.last_used_at = now
    db.add(device)
    await db.flush()
    await db.refresh(device)
    return device


async def delete_org_devices(db: AsyncSession, *, org_id: str) -> None:
    stmt = delete(UserMfaDevice).where(UserMfaDevice.org_id == org_id)
    await db.execute(stmt)


def compute_device_expiry(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


# ─── Recovery Codes ───────────────────────────────────────────────────────────


def _generate_recovery_code() -> str:
    """Generate a human-readable recovery code like 'A1B2C3D4'."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Exclude confusing chars: 0, O, 1, I
    code = "".join(secrets.choice(chars) for _ in range(RECOVERY_CODE_LENGTH))
    return f"{code[:4]}-{code[4:]}"


def _hash_recovery_code(code: str) -> str:
    """Hash a recovery code for storage."""
    normalized = code.upper().replace("-", "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def generate_recovery_codes(
    db: AsyncSession,
    *,
    identity_id,
) -> list[str]:
    """Generate new recovery codes for an identity, replacing any existing ones."""
    stmt = delete(UserMfaRecoveryCode).where(
        UserMfaRecoveryCode.identity_id == identity_id,
    )
    await db.execute(stmt)
    await db.flush()

    plain_codes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        code = _generate_recovery_code()
        plain_codes.append(code)
        recovery = UserMfaRecoveryCode(
            identity_id=identity_id,
            code_hash=_hash_recovery_code(code),
        )
        db.add(recovery)

    await db.flush()
    return plain_codes


async def verify_recovery_code(
    db: AsyncSession,
    *,
    identity_id,
    code: str,
) -> bool:
    """Verify and consume a recovery code. Returns True if valid."""
    code_hash = _hash_recovery_code(code)
    stmt = select(UserMfaRecoveryCode).where(
        UserMfaRecoveryCode.identity_id == identity_id,
        UserMfaRecoveryCode.code_hash == code_hash,
        UserMfaRecoveryCode.used_at.is_(None),
    )
    result = await db.execute(stmt)
    recovery = result.scalar_one_or_none()

    if not recovery:
        return False

    recovery.used_at = datetime.now(timezone.utc)
    db.add(recovery)
    await db.flush()
    return True


async def get_remaining_recovery_codes_count(
    db: AsyncSession,
    *,
    identity_id,
) -> int:
    """Get the count of unused recovery codes for an identity."""
    from sqlalchemy import func

    stmt = (
        select(func.count())
        .select_from(UserMfaRecoveryCode)
        .where(
            UserMfaRecoveryCode.identity_id == identity_id,
            UserMfaRecoveryCode.used_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def delete_user_recovery_codes(
    db: AsyncSession,
    *,
    identity_id,
) -> None:
    """Delete all recovery codes for an identity."""
    stmt = delete(UserMfaRecoveryCode).where(
        UserMfaRecoveryCode.identity_id == identity_id,
    )
    await db.execute(stmt)
    await db.flush()


async def delete_user_devices(
    db: AsyncSession,
    *,
    org_id: str,
    user_id,
) -> None:
    """Delete all trusted devices for a user in an org."""
    stmt = delete(UserMfaDevice).where(
        UserMfaDevice.org_id == org_id,
        UserMfaDevice.user_id == user_id,
    )
    await db.execute(stmt)


async def clear_user_mfa(
    db: AsyncSession,
    identity,
    *,
    org_id: str,
    user_id,
) -> None:
    """Clear all MFA data for an identity (secret, recovery codes) and
    per-org devices for the given user."""
    identity.mfa_enabled = False
    identity.mfa_secret_encrypted = None
    identity.mfa_method = None
    identity.mfa_confirmed_at = None
    db.add(identity)

    await delete_user_devices(db, org_id=org_id, user_id=user_id)
    await delete_user_recovery_codes(db, identity_id=identity.id)
    await db.flush()
