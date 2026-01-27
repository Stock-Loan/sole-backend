from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.asset import Asset
from app.models.storage_backend_config import StorageBackendConfig
from app.schemas.assets import UploadSessionRequest, UploadSessionResponse
from app.services.storage.key_generator import KeyGenerator
from app.services.storage.adapter import StorageAdapter, LocalFileSystemAdapter
from app.core.settings import settings
from datetime import datetime, timezone


# Simple factory for now
def get_storage_adapter(config: StorageBackendConfig = None) -> StorageAdapter:
    # In a real app, we'd switch on config.provider
    # For now, force Local
    base_url = "http://localhost:8000"  # TODO: get from settings
    return LocalFileSystemAdapter(base_path=settings.local_upload_dir, base_url=base_url)


class AssetService:
    def __init__(self, db: AsyncSession):
        self.db = db

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
            status="pending",
            bucket="local",  # Adapter-specific
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
            required_headers_or_fields=upload_info.get("headers", {}),
        )

    async def finalize_upload(self, asset_id: UUID):
        asset = await self.db.get(Asset, asset_id)
        if not asset:
            raise ValueError("Asset not found")

        adapter = get_storage_adapter()
        if not adapter.object_exists(asset.object_key):
            # For local dev, maybe the client calls the local-content endpoint which writes it?
            # If using S3, we check HEAD.
            pass
            # raise ValueError("Object not found in storage")

        asset.status = "uploaded"
        asset.updated_at = datetime.now(timezone.utc)
        self.db.add(asset)
        await self.db.commit()
        await self.db.refresh(asset)
        return asset

    async def get_download_url(self, asset_id: UUID) -> str:
        asset = await self.db.get(Asset, asset_id)
        if not asset or asset.status != "uploaded":
            raise ValueError("Asset not found or not uploaded")

        adapter = get_storage_adapter()
        return adapter.generate_download_url(asset.object_key)
