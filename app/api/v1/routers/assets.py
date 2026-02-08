from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query, status
from sqlalchemy import select
from app.schemas.assets import UploadSessionRequest, UploadSessionResponse, AssetRead
from app.services.storage.service import AssetService
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.storage.adapter import LocalFileSystemAdapter, verify_local_url_signature
from app.core.settings import settings
from app.api import deps
from app.models import Asset, User

router = APIRouter(prefix="/assets", tags=["assets"])


async def _require_asset_for_org(
    db: AsyncSession, *, asset_id: UUID | None = None, object_key: str | None = None, org_id: str
) -> Asset:
    if asset_id is None and object_key is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Asset lookup required")
    conditions = [Asset.org_id == org_id]
    if asset_id is not None:
        conditions.append(Asset.id == asset_id)
    if object_key is not None:
        conditions.append(Asset.object_key == object_key)
    stmt = select(Asset).where(*conditions)
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


@router.post("/upload-sessions", response_model=UploadSessionResponse)
async def create_upload_session(
    payload: UploadSessionRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
):
    if payload.org_id != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="org_id does not match tenant context",
        )
    payload = payload.model_copy(update={"org_id": ctx.org_id})
    service = AssetService(db)
    try:
        return await service.create_upload_session(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{asset_id}/finalize", response_model=AssetRead)
async def finalize_asset(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
):
    service = AssetService(db)
    try:
        asset = await service.finalize_upload(asset_id, org_id=ctx.org_id)
        return asset
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{asset_id}/download-url")
async def get_download_url(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
):
    service = AssetService(db)
    try:
        url = await service.get_download_url(asset_id, org_id=ctx.org_id)
        return {"download_url": url}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Local Dev Endpoints ---


@router.put("/local-content")
async def upload_local_content(
    request: Request,
    key: str = Query(...),
    expires: int = Query(...),
    signature: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
):
    if settings.storage_provider != "local":
        raise HTTPException(status_code=404, detail="Not supported")
    if not verify_local_url_signature(settings.secret_key, key, expires, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired URL signature",
        )
    await _require_asset_for_org(db, object_key=key, org_id=ctx.org_id)
    body = await request.body()

    adapter = LocalFileSystemAdapter(base_path=settings.local_upload_dir, base_url="")
    try:
        adapter.write_file(key, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(status_code=200)


@router.get("/local-content")
async def get_local_content(
    key: str = Query(...),
    expires: int = Query(...),
    signature: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
):
    if settings.storage_provider != "local":
        raise HTTPException(status_code=404, detail="Not supported")
    if not verify_local_url_signature(settings.secret_key, key, expires, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired URL signature",
        )
    await _require_asset_for_org(db, object_key=key, org_id=ctx.org_id)
    adapter = LocalFileSystemAdapter(base_path=settings.local_upload_dir, base_url="")
    try:
        path = adapter.resolve_path(key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not path.exists():
        raise HTTPException(status_code=404)
    from fastapi.responses import FileResponse

    return FileResponse(path)
