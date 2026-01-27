from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from app.schemas.assets import UploadSessionRequest, UploadSessionResponse, AssetRead
from app.services.storage.service import AssetService
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.storage.adapter import LocalFileSystemAdapter
from app.core.settings import settings
from pathlib import Path

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("/upload-sessions", response_model=UploadSessionResponse)
async def create_upload_session(
    payload: UploadSessionRequest,
    db: AsyncSession = Depends(get_db),
    # ctx: deps.TenantContext = Depends(deps.get_tenant_context), # Context check
):
    service = AssetService(db)
    try:
        return await service.create_upload_session(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{asset_id}/finalize", response_model=AssetRead)
async def finalize_asset(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    service = AssetService(db)
    try:
        asset = await service.finalize_upload(asset_id)
        return asset
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{asset_id}/download-url")
async def get_download_url(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    service = AssetService(db)
    try:
        url = await service.get_download_url(asset_id)
        return {"download_url": url}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Local Dev Endpoints ---


@router.put("/local-content")
async def upload_local_content(
    request: Request,
    key: str = Query(...),
):
    # This simulates S3 PUT
    # Security: In real dev, verify key structure.
    body = await request.body()

    adapter = LocalFileSystemAdapter(base_path=settings.local_upload_dir, base_url="")
    adapter.write_file(key, body)

    return Response(status_code=200)


@router.get("/local-content")
async def get_local_content(
    key: str = Query(...),
):
    # This simulates S3 GET
    adapter = LocalFileSystemAdapter(base_path=settings.local_upload_dir, base_url="")
    if not adapter.object_exists(key):
        raise HTTPException(status_code=404)

    path = Path(settings.local_upload_dir) / key
    # Simple file serve
    from fastapi.responses import FileResponse

    return FileResponse(path)
