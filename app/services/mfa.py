from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.user_mfa_device import UserMfaDevice


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
    await db.commit()
    await db.refresh(device)
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
    await db.commit()
    await db.refresh(device)
    return device


def compute_device_expiry(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)
