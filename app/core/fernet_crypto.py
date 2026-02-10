from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.settings import settings


_DEFAULT_DEV_KDF_SALT = "sole-fernet-dev-salt-v1"


def _effective_kdf_salt(secret: str) -> bytes:
    configured = (settings.fernet_kdf_salt or "").strip()
    if configured:
        return configured.encode("utf-8")
    # Development fallback only. Production settings validation requires
    # FERNET_KDF_SALT to be explicitly configured.
    fallback = f"{_DEFAULT_DEV_KDF_SALT}:{secret[:16]}"
    return fallback.encode("utf-8")


def _derive_pbkdf2_key(secret: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_effective_kdf_salt(secret),
        iterations=max(100_000, settings.fernet_kdf_iterations),
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8")))


def _derive_legacy_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=16)
def _primary_fernet_for_secret(secret: str) -> Fernet:
    return Fernet(_derive_pbkdf2_key(secret))


@lru_cache(maxsize=16)
def _legacy_fernet_for_secret(secret: str) -> Fernet:
    return Fernet(_derive_legacy_key(secret))


def get_primary_fernet(*, secret: str | None = None) -> Fernet:
    key_material = secret or settings.secret_key
    return _primary_fernet_for_secret(key_material)


def decrypt_fernet_token(token: bytes, *, secret: str | None = None) -> bytes:
    key_material = secret or settings.secret_key
    primary = _primary_fernet_for_secret(key_material)
    try:
        return primary.decrypt(token)
    except InvalidToken:
        # Backward-compatible decryption for data encrypted with the
        # legacy SHA-256-derived Fernet key.
        return _legacy_fernet_for_secret(key_material).decrypt(token)
