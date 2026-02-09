from __future__ import annotations

from pathlib import Path
from typing import Iterable
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.org_document_folder import OrgDocumentFolder
from app.models.org_document_template import OrgDocumentTemplate
from app.services.local_uploads import org_templates_subdir, save_upload
from app.services.storage.adapter import GCSStorageAdapter, LocalFileSystemAdapter
from app.core.settings import settings


DEFAULT_FOLDERS = [
    ("HR", "HR"),
    ("LEGAL", "Legal"),
    ("FINANCE", "Finance"),
    ("GENERAL", "General"),
]

ALLOWED_TEMPLATE_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}


def validate_template_filename(file_name: str | None) -> None:
    if not file_name:
        raise ValueError("file_name is required")
    ext = Path(file_name).suffix.lower()
    if ext not in ALLOWED_TEMPLATE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_TEMPLATE_EXTENSIONS))
        raise ValueError(f"File type not allowed. Allowed extensions: {allowed}")


async def ensure_default_folders(db: AsyncSession, org_id: str) -> list[OrgDocumentFolder]:
    stmt = select(OrgDocumentFolder).where(OrgDocumentFolder.org_id == org_id)
    existing = {folder.system_key: folder for folder in (await db.execute(stmt)).scalars().all()}
    created: list[OrgDocumentFolder] = []
    for key, name in DEFAULT_FOLDERS:
        if existing.get(key):
            continue
        folder = OrgDocumentFolder(
            org_id=org_id,
            name=name,
            system_key=key,
            is_system=True,
        )
        db.add(folder)
        created.append(folder)
    if created:
        await db.commit()
    return created


async def list_folders(
    db: AsyncSession,
    ctx: deps.TenantContext,
) -> list[OrgDocumentFolder]:
    await ensure_default_folders(db, ctx.org_id)
    stmt = (
        select(OrgDocumentFolder)
        .where(OrgDocumentFolder.org_id == ctx.org_id)
        .order_by(OrgDocumentFolder.is_system.desc(), OrgDocumentFolder.name.asc())
    )
    return (await db.execute(stmt)).scalars().all()


async def folder_template_counts(
    db: AsyncSession,
    ctx: deps.TenantContext,
    folder_ids: Iterable[UUID],
) -> dict[UUID, int]:
    if not folder_ids:
        return {}
    stmt = (
        select(OrgDocumentTemplate.folder_id, func.count())
        .where(
            OrgDocumentTemplate.org_id == ctx.org_id,
            OrgDocumentTemplate.folder_id.in_(list(folder_ids)),
        )
        .group_by(OrgDocumentTemplate.folder_id)
    )
    result = await db.execute(stmt)
    return {row[0]: row[1] for row in result.all()}


async def get_folder(
    db: AsyncSession, ctx: deps.TenantContext, folder_id: UUID
) -> OrgDocumentFolder | None:
    stmt = select(OrgDocumentFolder).where(
        OrgDocumentFolder.org_id == ctx.org_id,
        OrgDocumentFolder.id == folder_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_folder(
    db: AsyncSession,
    ctx: deps.TenantContext,
    name: str,
) -> OrgDocumentFolder:
    folder = OrgDocumentFolder(org_id=ctx.org_id, name=name, is_system=False)
    db.add(folder)
    await db.flush()
    await db.refresh(folder)
    return folder


async def update_folder(
    db: AsyncSession,
    ctx: deps.TenantContext,
    folder: OrgDocumentFolder,
    name: str,
) -> OrgDocumentFolder:
    folder.name = name
    db.add(folder)
    await db.flush()
    await db.refresh(folder)
    return folder


async def delete_folder(
    db: AsyncSession,
    ctx: deps.TenantContext,
    folder: OrgDocumentFolder,
) -> None:
    stmt = (
        select(func.count())
        .select_from(OrgDocumentTemplate)
        .where(
            OrgDocumentTemplate.org_id == ctx.org_id,
            OrgDocumentTemplate.folder_id == folder.id,
        )
    )
    count = (await db.execute(stmt)).scalar_one() or 0
    if count:
        raise ValueError("Folder contains templates")
    await db.delete(folder)
    await db.flush()


async def list_templates(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    folder_id: UUID | None = None,
) -> list[OrgDocumentTemplate]:
    stmt = (
        select(OrgDocumentTemplate)
        .options(selectinload(OrgDocumentTemplate.uploaded_by_user))
        .where(OrgDocumentTemplate.org_id == ctx.org_id)
    )
    if folder_id:
        stmt = stmt.where(OrgDocumentTemplate.folder_id == folder_id)
    stmt = stmt.order_by(OrgDocumentTemplate.created_at.desc())
    return (await db.execute(stmt)).scalars().all()


async def get_template(
    db: AsyncSession,
    ctx: deps.TenantContext,
    template_id: UUID,
) -> OrgDocumentTemplate | None:
    stmt = (
        select(OrgDocumentTemplate)
        .options(selectinload(OrgDocumentTemplate.uploaded_by_user))
        .where(
            OrgDocumentTemplate.org_id == ctx.org_id,
            OrgDocumentTemplate.id == template_id,
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_template_from_upload(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    folder_id: UUID | None,
    name: str | None,
    description: str | None,
    file: UploadFile,
    actor_id: UUID,
    base_dir: Path,
) -> OrgDocumentTemplate:
    subdir = org_templates_subdir(ctx.org_id, folder_id)
    relative_path, original_name = await save_upload(
        file,
        base_dir=base_dir,
        subdir=subdir,
        allowed_extensions=ALLOWED_TEMPLATE_EXTENSIONS,
        max_size_bytes=settings.max_upload_size_mb * 1024 * 1024,
    )
    template = OrgDocumentTemplate(
        org_id=ctx.org_id,
        folder_id=folder_id,
        name=name or original_name,
        description=description,
        file_name=original_name,
        storage_path_or_url=relative_path,
        storage_provider="local",
        storage_bucket=None,
        storage_object_key=relative_path,
        content_type=file.content_type,
        size_bytes=getattr(file, "size", None),
        checksum=None,
        uploaded_by_user_id=actor_id,
    )
    db.add(template)
    await db.flush()
    await db.refresh(template)
    return template


async def create_template_from_storage(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    folder_id: UUID | None,
    name: str | None,
    description: str | None,
    file_name: str,
    storage_key: str,
    storage_provider: str | None,
    storage_bucket: str | None,
    content_type: str | None,
    size_bytes: int | None,
    checksum: str | None,
    actor_id: UUID,
) -> OrgDocumentTemplate:
    validate_template_filename(file_name)
    template = OrgDocumentTemplate(
        org_id=ctx.org_id,
        folder_id=folder_id,
        name=name or file_name,
        description=description,
        file_name=file_name,
        storage_path_or_url=storage_key,
        storage_provider=storage_provider,
        storage_bucket=storage_bucket,
        storage_object_key=storage_key,
        content_type=content_type,
        size_bytes=size_bytes,
        checksum=checksum,
        uploaded_by_user_id=actor_id,
    )
    db.add(template)
    await db.flush()
    await db.refresh(template)
    return template


async def delete_template(
    db: AsyncSession,
    ctx: deps.TenantContext,
    template: OrgDocumentTemplate,
) -> None:
    if template.storage_object_key:
        try:
            adapter = _adapter_for_template(template.storage_provider, template.storage_bucket)
            if adapter:
                adapter.delete_object(template.storage_object_key)
        except Exception:
            # Best effort cleanup; keep going so deletes are not blocked.
            pass
    await db.delete(template)
    await db.flush()


def _adapter_for_template(provider: str | None, bucket: str | None):
    if (provider or "").lower() == "gcs":
        resolved_bucket = bucket or settings.gcs_bucket
        if not resolved_bucket:
            return None
        return GCSStorageAdapter(
            bucket=resolved_bucket,
            signed_url_expiry_seconds=settings.gcs_signed_url_expiry_seconds,
        )
    return LocalFileSystemAdapter(
        base_path=settings.local_upload_dir, base_url=settings.public_base_url
    )
