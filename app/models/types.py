import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

from app.core.settings import settings


def _derive_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet(secret: Optional[str] = None) -> Fernet:
    key_material = secret or settings.secret_key
    return Fernet(_derive_key(key_material))


class EncryptedString(TypeDecorator):
    """Transparent encryption/decryption for string fields using Fernet."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, *, secret: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._secret = secret

    @property
    def _fernet(self) -> Fernet:
        return _get_fernet(self._secret)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        token = self._fernet.encrypt(str(value).encode("utf-8"))
        return bytes(token)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            decrypted = self._fernet.decrypt(value)
            return decrypted.decode("utf-8")
        except InvalidToken as exc:  # pragma: no cover - indicates corrupted data
            raise ValueError("Unable to decrypt value") from exc


__all__ = ["EncryptedString"]
