import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, exists
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.announcement import Announcement, AnnouncementRead
from app.models.user import User
from app.schemas.announcements import (
    AnnouncementCreate,
    AnnouncementListResponse,
    AnnouncementOut,
    AnnouncementUpdate,
)
from app.services import announcements as announcement_service
from app.services import announcement_stream
from app.services.audit import model_snapshot, record_audit_log
from app.services import authz

router = APIRouter(prefix="/announcements", tags=["announcements"])
logger = logging.getLogger(__name__)


def _announcement_event_payload(announcement: Announcement) -> dict:
    return {
        "type": "announcement.published",
        "id": str(announcement.id),
        "org_id": announcement.org_id,
        "title": announcement.title,
        "announcement_type": announcement.type,
        "status": announcement.status,
        "published_at": (
            announcement.published_at.isoformat() if announcement.published_at else None
        ),
    }


async def _maybe_publish_announcement(announcement: Announcement) -> None:
    if announcement.status != "PUBLISHED":
        return
    await announcement_stream.publish_announcement(
        announcement.org_id, _announcement_event_payload(announcement)
    )


async def _get_announcement_or_404(
    db: AsyncSession,
    ctx: deps.TenantContext,
    announcement_id: UUID,
) -> Announcement:
    stmt = select(Announcement).where(
        Announcement.id == announcement_id, Announcement.org_id == ctx.org_id
    )
    result = await db.execute(stmt)
    announcement = result.scalar_one_or_none()
    if not announcement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    return announcement


@router.get("", response_model=AnnouncementListResponse, summary="List announcements")
async def list_announcements(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementListResponse:
    filters = [Announcement.org_id == ctx.org_id]
    filters.append(Announcement.status == "PUBLISHED")

    offset = (page - 1) * page_size
    base_stmt = select(Announcement).where(*filters)
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    result = await db.execute(base_stmt.offset(offset).limit(page_size))
    announcements = result.scalars().all()
    read_counts = await announcement_service.get_read_counts(db, ctx, [a.id for a in announcements])
    target_count = await announcement_service.get_recipient_count(db, ctx)
    for announcement in announcements:
        announcement.read_count = read_counts.get(str(announcement.id), 0)
        announcement.target_count = target_count
    return AnnouncementListResponse(items=announcements, total=total)


@router.get(
    "/stream",
    summary="Stream published announcements (SSE)",
)
async def stream_announcements(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_authenticated_user),
):
    channel = announcement_stream.channel_for_org(ctx.org_id)
    pubsub = await announcement_stream.subscribe(channel)

    async def event_generator():
        try:
            yield ": connected\n\n"
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15.0
                )
                if message and message.get("data"):
                    yield "event: announcement.published\n"
                    yield f"data: {message['data']}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0)
        finally:
            await announcement_stream.unsubscribe(pubsub, channel)

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@router.get(
    "/admin",
    response_model=AnnouncementListResponse,
    summary="Admin list of announcements (all statuses)",
)
async def list_admin_announcements(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ANNOUNCEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementListResponse:
    filters = [Announcement.org_id == ctx.org_id]

    offset = (page - 1) * page_size
    base_stmt = select(Announcement).where(*filters)
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    result = await db.execute(base_stmt.offset(offset).limit(page_size))
    announcements = result.scalars().all()
    read_counts = await announcement_service.get_read_counts(db, ctx, [a.id for a in announcements])
    target_count = await announcement_service.get_recipient_count(db, ctx)
    for announcement in announcements:
        announcement.read_count = read_counts.get(str(announcement.id), 0)
        announcement.target_count = target_count
    return AnnouncementListResponse(items=announcements, total=total)


@router.get(
    "/unread",
    response_model=AnnouncementListResponse,
    summary="List unread announcements for current user",
)
async def list_unread_announcements(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementListResponse:
    offset = (page - 1) * page_size
    unread_filter = ~exists().where(
        AnnouncementRead.announcement_id == Announcement.id,
        AnnouncementRead.user_id == current_user.id,
        AnnouncementRead.org_id == ctx.org_id,
    )
    base_stmt = select(Announcement).where(
        Announcement.org_id == ctx.org_id,
        Announcement.status == "PUBLISHED",
        unread_filter,
    )
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    result = await db.execute(base_stmt.offset(offset).limit(page_size))
    announcements = result.scalars().all()
    read_counts = await announcement_service.get_read_counts(db, ctx, [a.id for a in announcements])
    target_count = await announcement_service.get_recipient_count(db, ctx)
    for announcement in announcements:
        announcement.read_count = read_counts.get(str(announcement.id), 0)
        announcement.target_count = target_count
    return AnnouncementListResponse(items=announcements, total=total)


@router.get("/unread/count", summary="Get unread announcement count for current user")
async def unread_count(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    unread_filter = ~exists().where(
        AnnouncementRead.announcement_id == Announcement.id,
        AnnouncementRead.user_id == current_user.id,
        AnnouncementRead.org_id == ctx.org_id,
    )
    count_stmt = (
        select(func.count())
        .select_from(Announcement)
        .where(
            Announcement.org_id == ctx.org_id,
            Announcement.status == "PUBLISHED",
            unread_filter,
        )
    )
    unread = (await db.execute(count_stmt)).scalar_one()
    return {"unread": unread}


@router.get("/{announcement_id}", response_model=AnnouncementOut, summary="Get an announcement")
async def get_announcement(
    announcement_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementOut:
    announcement = await _get_announcement_or_404(db, ctx, announcement_id)
    has_manage = await authz.check_permission(
        current_user, ctx, PermissionCode.ANNOUNCEMENT_MANAGE, db
    )
    if not has_manage and announcement.status != "PUBLISHED":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")

    read_counts = await announcement_service.get_read_counts(db, ctx, [announcement.id])
    target_count = await announcement_service.get_recipient_count(db, ctx)
    announcement.read_count = read_counts.get(str(announcement.id), 0)
    announcement.target_count = target_count
    return announcement


@router.post("", response_model=AnnouncementOut, status_code=201, summary="Create an announcement")
async def create_announcement(
    payload: AnnouncementCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ANNOUNCEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementOut:
    announcement = await announcement_service.create_announcement(db, ctx, payload)
    announcement.read_count = 0
    announcement.target_count = await announcement_service.get_recipient_count(db, ctx)
    logger.info(
        "Announcement created",
        extra={
            "org_id": ctx.org_id,
            "announcement_id": str(announcement.id),
            "status": announcement.status,
        },
    )
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="announcement.created",
        resource_type="announcement",
        resource_id=str(announcement.id),
        old_value=None,
        new_value=model_snapshot(announcement),
    )
    await db.commit()
    await _maybe_publish_announcement(announcement)
    return announcement


@router.patch(
    "/{announcement_id}", response_model=AnnouncementOut, summary="Update an announcement or status"
)
async def update_announcement(
    announcement_id: UUID,
    payload: AnnouncementUpdate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ANNOUNCEMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementOut:
    announcement = await _get_announcement_or_404(db, ctx, announcement_id)
    old_snapshot = model_snapshot(announcement)
    announcement = await announcement_service.update_announcement(db, announcement, payload)
    read_counts = await announcement_service.get_read_counts(db, ctx, [announcement.id])
    target_count = await announcement_service.get_recipient_count(db, ctx)
    announcement.read_count = read_counts.get(str(announcement.id), 0)
    announcement.target_count = target_count
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="announcement.updated",
        resource_type="announcement",
        resource_id=str(announcement.id),
        old_value=old_snapshot,
        new_value=model_snapshot(announcement),
    )
    await db.commit()
    await _maybe_publish_announcement(announcement)
    return announcement


@router.post(
    "/{announcement_id}/read",
    summary="Mark announcement as read",
)
async def mark_read(
    announcement_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    announcement = await _get_announcement_or_404(db, ctx, announcement_id)
    if announcement.status != "PUBLISHED":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    await announcement_service.record_read(db, ctx, announcement.id, current_user.id)
    return {"status": "ok"}
