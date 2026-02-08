from uuid import UUID, uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.asset import Asset
from app.models.storage_backend_config import StorageBackendConfig
from app.schemas.assets import UploadSessionRequest, UploadSessionResponse
from app.services.storage.key_generator import KeyGenerator
from app.services.storage.adapter import StorageAdapter, LocalFileSystemAdapter, GCSStorageAdapter
from app.core.settings import settings
from datetime import datetime, timezone


# Simple factory for now
def get_storage_adapter(
    config: StorageBackendConfig | None = None,
    *,
    bucket_override: str | None = None,
) -> StorageAdapter:
    # In a real app, we'd resolve org-specific config. For now, rely on settings.
    provider = settings.storage_provider
    if config and config.provider:
        provider = config.provider

    if provider == "gcs":
        bucket = bucket_override or (config.bucket if config else None) or settings.gcs_bucket
        if not bucket:
            raise ValueError("GCS bucket is not configured")
        return GCSStorageAdapter(
            bucket=bucket,
            signed_url_expiry_seconds=settings.gcs_signed_url_expiry_seconds,
        )

    base_url = settings.public_base_url
    return LocalFileSystemAdapter(
        base_path=settings.local_upload_dir,
        base_url=base_url,
        signing_key=settings.secret_key,
    )


class AssetService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_asset(self, asset_id: UUID, org_id: str | None = None) -> Asset | None:
        if org_id:
            stmt = select(Asset).where(Asset.id == asset_id, Asset.org_id == org_id)
            result = await self.db.execute(stmt)
            return result.scalar_one_or_none()
        return await self.db.get(Asset, asset_id)

    async def create_upload_session(self, req: UploadSessionRequest) -> UploadSessionResponse:
        asset_id = uuid4()

        # 1. Generate Object Key
        object_key = KeyGenerator.generate_object_key(
            org_id=req.org_id,
            kind=req.kind,
            asset_id=asset_id,
            filename=req.filename,
            owner_refs=req.owner_refs,
        )

        # 2. Resolve Config (Mock for now, assume Default)
        # config = await self.db.execute(select(StorageBackendConfig)...)
        adapter = get_storage_adapter()

        # 3. Create Asset Record
        asset = Asset(
            id=asset_id,
            org_id=req.org_id,
            owner_type="user",  # TODO: Infer from kind or req
            owner_id=req.owner_refs.get("user_id") or req.org_id,  # Fallback
            kind=req.kind,
            filename=req.filename,
            content_type=req.content_type,
            size_bytes=req.size_bytes,
            checksum=req.checksum,
            status="pending",
            provider=adapter.provider,
            bucket=adapter.bucket,
            object_key=object_key,
        )
        self.db.add(asset)
        await self.db.commit()
        await self.db.refresh(asset)

        # 4. Generate URL
        upload_info = adapter.generate_upload_url(
            object_key=object_key, content_type=req.content_type, size_bytes=req.size_bytes
        )

        return UploadSessionResponse(
            asset_id=asset.id,
            upload_url=upload_info["upload_url"],
            storage_provider=adapter.provider,
            storage_bucket=adapter.bucket,
            object_key=object_key,
            required_headers_or_fields=upload_info.get("headers", {}),
        )

    async def finalize_upload(self, asset_id: UUID, *, org_id: str):
        asset = await self._get_asset(asset_id, org_id)
        if not asset:
            raise ValueError("Asset not found")

        adapter = get_storage_adapter(bucket_override=asset.bucket)
        if not adapter.object_exists(asset.object_key):
            raise ValueError("Object not found in storage")

        asset.status = "uploaded"
        asset.updated_at = datetime.now(timezone.utc)
        self.db.add(asset)
        await self.db.commit()
        await self.db.refresh(asset)
        return asset

    async def get_download_url(self, asset_id: UUID, *, org_id: str) -> str:
        asset = await self._get_asset(asset_id, org_id)
        if not asset or asset.status != "uploaded":
            raise ValueError("Asset not found or not uploaded")

        adapter = get_storage_adapter(bucket_override=asset.bucket)
        return adapter.generate_download_url(
            asset.object_key, expires_in=settings.gcs_signed_url_expiry_seconds
        )
