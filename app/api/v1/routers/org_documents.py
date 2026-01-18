from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.core.settings import settings
from app.db.session import get_db
from app.schemas.org_documents import (
    OrgDocumentFolderCreate,
    OrgDocumentFolderDTO,
    OrgDocumentFolderListResponse,
    OrgDocumentFolderUpdate,
    OrgDocumentTemplateDTO,
    OrgDocumentTemplateListResponse,
)
from app.services import org_documents
from app.services.local_uploads import resolve_local_path


router = APIRouter(prefix="/org/documents", tags=["org-documents"])


@router.get(
    "/folders",
    response_model=OrgDocumentFolderListResponse,
    summary="List org document folders",
)
async def list_folders(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> OrgDocumentFolderListResponse:
    folders = await org_documents.list_folders(db, ctx)
    counts = await org_documents.folder_template_counts(
        db, ctx, folder_ids=[folder.id for folder in folders]
    )
    items = [
        OrgDocumentFolderDTO.model_validate(folder).model_copy(
            update={"template_count": counts.get(folder.id, 0)}
        )
        for folder in folders
    ]
    return OrgDocumentFolderListResponse(items=items, total=len(items))


@router.post(
    "/folders",
    response_model=OrgDocumentFolderDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Create an org document folder",
)
async def create_folder(
    payload: OrgDocumentFolderCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> OrgDocumentFolderDTO:
    folder = await org_documents.create_folder(db, ctx, payload.name)
    return OrgDocumentFolderDTO.model_validate(folder)


@router.patch(
    "/folders/{folder_id}",
    response_model=OrgDocumentFolderDTO,
    summary="Update an org document folder",
)
async def update_folder(
    folder_id: UUID,
    payload: OrgDocumentFolderUpdate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> OrgDocumentFolderDTO:
    folder = await org_documents.get_folder(db, ctx, folder_id)
    if not folder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    if folder.is_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="System folders cannot be renamed")
    updated = await org_documents.update_folder(db, ctx, folder, payload.name)
    return OrgDocumentFolderDTO.model_validate(updated)


@router.delete(
    "/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an org document folder",
)
async def delete_folder(
    folder_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    folder = await org_documents.get_folder(db, ctx, folder_id)
    if not folder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    if folder.is_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="System folders cannot be deleted")
    try:
        await org_documents.delete_folder(db, ctx, folder)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return None


@router.get(
    "/templates",
    response_model=OrgDocumentTemplateListResponse,
    summary="List org document templates",
)
async def list_templates(
    folder_id: UUID | None = None,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> OrgDocumentTemplateListResponse:
    templates = await org_documents.list_templates(db, ctx, folder_id=folder_id)
    items = [OrgDocumentTemplateDTO.model_validate(template) for template in templates]
    return OrgDocumentTemplateListResponse(items=items, total=len(items))


@router.post(
    "/templates/upload",
    response_model=OrgDocumentTemplateDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an org document template",
)
async def upload_template(
    file: UploadFile = File(...),
    folder_id: UUID | None = Form(default=None),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> OrgDocumentTemplateDTO:
    if folder_id:
        folder = await org_documents.get_folder(db, ctx, folder_id)
        if not folder:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    template = await org_documents.create_template_from_upload(
        db,
        ctx,
        folder_id=folder_id,
        name=name,
        description=description,
        file=file,
        actor_id=current_user.id,
        base_dir=Path(settings.local_upload_dir),
    )
    return OrgDocumentTemplateDTO.model_validate(template)


@router.get(
    "/templates/{template_id}",
    response_model=OrgDocumentTemplateDTO,
    summary="Get org document template metadata",
)
async def get_template(
    template_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> OrgDocumentTemplateDTO:
    template = await org_documents.get_template(db, ctx, template_id)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return OrgDocumentTemplateDTO.model_validate(template)


@router.get(
    "/templates/{template_id}/download",
    response_class=FileResponse,
    summary="Download org document template file",
)
async def download_template(
    template_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    template = await org_documents.get_template(db, ctx, template_id)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    if template.storage_path_or_url.startswith("http"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "document_not_local",
                "message": "Template is stored externally",
                "details": {"storage_path_or_url": template.storage_path_or_url},
            },
        )
    try:
        file_path = resolve_local_path(Path(settings.local_upload_dir), template.storage_path_or_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_document_path", "message": "Template path is invalid", "details": {}},
        ) from exc
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "document_missing", "message": "Template file does not exist", "details": {}},
        )
    return FileResponse(file_path, filename=template.file_name, media_type="application/octet-stream")


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an org document template",
)
async def delete_template(
    template_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: object = Depends(deps.require_permission(PermissionCode.ORG_DOCUMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    template = await org_documents.get_template(db, ctx, template_id)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    await org_documents.delete_template(db, ctx, template)
    return None
