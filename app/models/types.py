from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

from app.core.fernet_crypto import decrypt_fernet_token, get_primary_fernet


@lru_cache(maxsize=16)
def _get_fernet(secret: Optional[str] = None) -> Fernet:
    return get_primary_fernet(secret=secret)


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
            decrypted = decrypt_fernet_token(value, secret=self._secret)
            return decrypted.decode("utf-8")
        except InvalidToken as exc:  # pragma: no cover - indicates corrupted data
            raise ValueError("Unable to decrypt value") from exc


__all__ = ["EncryptedString"]
