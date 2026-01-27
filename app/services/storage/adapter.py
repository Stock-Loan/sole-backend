from abc import ABC, abstractmethod
from typing import Any, Dict
from pathlib import Path


class StorageAdapter(ABC):
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
    def __init__(self, base_path: str, base_url: str):
        self.base_path = Path(base_path)
        self.base_url = base_url.rstrip("/")
        self.base_path.mkdir(parents=True, exist_ok=True)

    def generate_upload_url(
        self, object_key: str, content_type: str, size_bytes: int
    ) -> Dict[str, Any]:
        # For local, we direct them to a backend endpoint that handles the write
        # We need to sign this or just use the asset ID if we trust the flow.
        # Ideally, we return a URL like /api/v1/assets/local-upload?key=...&sig=...
        # For simplicity in this prototype, we'll assume the API has a general handler.

        # We'll encode the key in the URL.
        # WARNING: In a real app, this should be a signed URL to prevent tampering.
        # Since we don't have a signing mechanism handy here without circular deps,
        # we will rely on the asset_id being checked at the endpoint.

        # Actually, the user prompts says:
        # "returns { assetId, uploadUrl, requiredHeadersOrFields }"
        # And then "Client uploads directly to storage."

        # For local, "storage" is the API server.
        # Let's say we have an endpoint PUT /api/v1/assets/content/{object_key_base64}

        # Minimal implementation:
        return {
            "upload_url": f"{self.base_url}/api/v1/assets/local-content?key={object_key}",
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "fields": {},
        }

    def generate_download_url(self, object_key: str, expires_in: int = 3600) -> str:
        # Local download endpoint
        return f"{self.base_url}/api/v1/assets/local-content?key={object_key}"

    def delete_object(self, object_key: str):
        path = self.base_path / object_key
        if path.exists():
            path.unlink()

    def object_exists(self, object_key: str) -> bool:
        path = self.base_path / object_key
        return path.exists()

    def write_file(self, object_key: str, content: bytes):
        path = self.base_path / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
