from abc import ABC, abstractmethod
from typing import Any, Dict
from pathlib import Path, PurePosixPath
from datetime import timedelta
import hashlib
import hmac
import time
from urllib.parse import urlencode


def _sign_local_url(secret_key: str, object_key: str, expires: int) -> str:
    """Create HMAC-SHA256 signature for a local storage URL."""
    message = f"{object_key}:{expires}"
    return hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_local_url_signature(
    secret_key: str, object_key: str, expires: int, signature: str
) -> bool:
    """Verify HMAC signature and expiry for a local storage URL.

    Returns False if the signature is invalid or the URL has expired.
    """
    if int(time.time()) > expires:
        return False
    expected = _sign_local_url(secret_key, object_key, expires)
    return hmac.compare_digest(expected, signature)


class StorageAdapter(ABC):
    provider: str = "local"
    bucket: str | None = None

    @abstractmethod
    def generate_upload_url(
        self, object_key: str, content_type: str, size_bytes: int
    ) -> Dict[str, Any]:
        """
        Returns:
            {
                "upload_url": "...",
                "method": "PUT" or "POST",
                "headers": {...},
                "fields": {...} # for POST form fields (S3)
            }
        """
        pass

    @abstractmethod
    def generate_download_url(self, object_key: str, expires_in: int = 3600) -> str:
        pass

    @abstractmethod
    def delete_object(self, object_key: str):
        pass

    @abstractmethod
    def object_exists(self, object_key: str) -> bool:
        pass


class LocalFileSystemAdapter(StorageAdapter):
    def __init__(self, base_path: str, base_url: str, *, signing_key: str = ""):
        self.base_path = Path(base_path)
        self.base_url = base_url.rstrip("/")
        self.signing_key = signing_key
        self.provider = "local"
        self.bucket = "local"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(self, object_key: str) -> Path:
        if "\\" in object_key:
            raise ValueError("Invalid object key")
        key_path = PurePosixPath(object_key)
        if key_path.is_absolute() or ".." in key_path.parts:
            raise ValueError("Invalid object key")
        base = self.base_path.resolve()
        resolved = (base / Path(object_key)).resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError("Invalid object key")
        return resolved

    def resolve_path(self, object_key: str) -> Path:
        return self._resolve_safe_path(object_key)

    def generate_upload_url(
        self, object_key: str, content_type: str, size_bytes: int
    ) -> Dict[str, Any]:
        expires = int(time.time()) + 900  # 15-minute window for upload
        sig = _sign_local_url(self.signing_key, object_key, expires)
        params = urlencode({"key": object_key, "expires": expires, "signature": sig})
        return {
            "upload_url": f"{self.base_url}/api/v1/assets/local-content?{params}",
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "fields": {},
        }

    def generate_download_url(self, object_key: str, expires_in: int = 3600) -> str:
        expires = int(time.time()) + expires_in
        sig = _sign_local_url(self.signing_key, object_key, expires)
        params = urlencode({"key": object_key, "expires": expires, "signature": sig})
        return f"{self.base_url}/api/v1/assets/local-content?{params}"

    def delete_object(self, object_key: str):
        path = self._resolve_safe_path(object_key)
        if path.exists():
            path.unlink()

    def object_exists(self, object_key: str) -> bool:
        try:
            path = self._resolve_safe_path(object_key)
        except ValueError:
            return False
        return path.exists()

    def write_file(self, object_key: str, content: bytes):
        path = self._resolve_safe_path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class GCSStorageAdapter(StorageAdapter):
    def __init__(self, bucket: str, *, signed_url_expiry_seconds: int = 900):
        # Lazy import to avoid requiring dependency unless used
        from google.cloud import storage
        import google.auth
        import google.auth.transport.requests

        self.provider = "gcs"
        self.bucket = bucket
        self.signed_url_expiry_seconds = signed_url_expiry_seconds
        self.credentials, _ = google.auth.default()
        self._auth_request = google.auth.transport.requests.Request()
        self.client = storage.Client(credentials=self.credentials)
        self._bucket_ref = self.client.bucket(bucket)

    def _signing_kwargs(self) -> Dict[str, Any]:
        # If the credentials can sign directly (service account key), use them.
        if hasattr(self.credentials, "sign_bytes"):
            return {"credentials": self.credentials}

        # Otherwise, use IAM SignBlob via access token + service account email.
        if not self.credentials.valid or self.credentials.expired or not self.credentials.token:
            self.credentials.refresh(self._auth_request)

        service_account_email = getattr(self.credentials, "service_account_email", None)
        if not service_account_email:
            raise RuntimeError(
                "GCS signed URL requires service account email; ensure the Cloud Run "
                "service account is available to ADC."
            )

        return {
            "service_account_email": service_account_email,
            "access_token": self.credentials.token,
        }

    def generate_upload_url(
        self, object_key: str, content_type: str, size_bytes: int
    ) -> Dict[str, Any]:
        blob = self._bucket_ref.blob(object_key)
        url = blob.generate_signed_url(
            expiration=timedelta(seconds=self.signed_url_expiry_seconds),
            method="PUT",
            content_type=content_type,
            **self._signing_kwargs(),
        )
        return {
            "upload_url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "fields": {},
        }

    def generate_download_url(self, object_key: str, expires_in: int = 3600) -> str:
        blob = self._bucket_ref.blob(object_key)
        return blob.generate_signed_url(
            expiration=timedelta(seconds=expires_in),
            method="GET",
            **self._signing_kwargs(),
        )

    def delete_object(self, object_key: str):
        blob = self._bucket_ref.blob(object_key)
        blob.delete()

    def object_exists(self, object_key: str) -> bool:
        blob = self._bucket_ref.blob(object_key)
        return blob.exists()
