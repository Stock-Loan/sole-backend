from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.announcement import Announcement, AnnouncementRead
from app.models.org_membership import OrgMembership
from app.schemas.announcements import (
    AnnouncementCreate,
    AnnouncementUpdate,
    ALLOWED_STATUSES,
    ALLOWED_TYPES,
)

DEFAULT_STATUS = "DRAFT"
DEFAULT_TYPE = "GENERAL"


def _normalize_status(status: str | None) -> str:
    if status is None:
        return DEFAULT_STATUS
    normalized = status.strip().upper()
    if normalized not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid status. Allowed: {sorted(ALLOWED_STATUSES)}")
    return normalized


def _normalize_type(type_value: str | None) -> str:
    if type_value is None:
        return DEFAULT_TYPE
    normalized = type_value.strip().upper()
    if normalized not in ALLOWED_TYPES:
        raise ValueError(f"Invalid type. Allowed: {sorted(ALLOWED_TYPES)}")
    return normalized


def _apply_status_transition(announcement: Announcement, new_status: str) -> None:
    now = datetime.now(timezone.utc)
    new_status = _normalize_status(new_status)
    if new_status == "PUBLISHED":
        if announcement.published_at is None:
            announcement.published_at = now
    else:
        # For unpublished/archived/draft we clear published_at
        announcement.published_at = None
    announcement.status = new_status


async def create_announcement(
    db: AsyncSession,
    ctx: deps.TenantContext,
    payload: AnnouncementCreate,
) -> Announcement:
    status = _normalize_status(payload.status)
    announcement = Announcement(
        org_id=ctx.org_id,
        title=payload.title.strip(),
        body=payload.body.strip(),
        status=status,
        type=_normalize_type(payload.type),
        scheduled_at=payload.scheduled_at,
    )
    _apply_status_transition(announcement, status)
    db.add(announcement)
    await db.flush()
    await db.refresh(announcement)
    return announcement


async def update_announcement(
    db: AsyncSession,
    announcement: Announcement,
    payload: AnnouncementUpdate,
) -> Announcement:
    data = payload.model_dump(exclude_unset=True)
    if "title" in data and data["title"]:
        announcement.title = data["title"]
    if "body" in data and data["body"]:
        announcement.body = data["body"]
    if "scheduled_at" in data:
        announcement.scheduled_at = data["scheduled_at"]
    if "status" in data and data["status"]:
        _apply_status_transition(announcement, data["status"])
    if "type" in data and data["type"]:
        announcement.type = _normalize_type(data["type"])
    await db.flush()
    await db.refresh(announcement)
    return announcement


async def record_read(
    db: AsyncSession,
    ctx: deps.TenantContext,
    announcement_id,
    user_id,
) -> AnnouncementRead:
    stmt = select(AnnouncementRead).where(
        AnnouncementRead.org_id == ctx.org_id,
        AnnouncementRead.announcement_id == announcement_id,
        AnnouncementRead.user_id == user_id,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    read = AnnouncementRead(
        announcement_id=announcement_id,
        org_id=ctx.org_id,
        user_id=user_id,
    )
    db.add(read)
    try:
        await db.commit()  # commit-ok: IntegrityError upsert with own rollback
    except IntegrityError:
        await db.rollback()
        # Another request likely created it; return existing row
        stmt = select(AnnouncementRead).where(
            AnnouncementRead.org_id == ctx.org_id,
            AnnouncementRead.announcement_id == announcement_id,
            AnnouncementRead.user_id == user_id,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return existing
        raise
    await db.refresh(read)
    return read


async def get_read_counts(
    db: AsyncSession,
    ctx: deps.TenantContext,
    announcement_ids: Iterable,
) -> dict[str, int]:
    ids = list(announcement_ids)
    if not ids:
        return {}
    stmt = (
        select(AnnouncementRead.announcement_id, func.count())
        .where(
            AnnouncementRead.org_id == ctx.org_id,
            AnnouncementRead.announcement_id.in_(ids),
        )
        .group_by(AnnouncementRead.announcement_id)
    )
    result = await db.execute(stmt)
    return {str(row[0]): int(row[1]) for row in result.all()}


async def get_recipient_count(db: AsyncSession, ctx: deps.TenantContext) -> int:
    stmt = (
        select(func.count())
        .select_from(OrgMembership)
        .where(
            OrgMembership.org_id == ctx.org_id,
            func.upper(OrgMembership.platform_status) == "ACTIVE",
        )
    )
    return int((await db.execute(stmt)).scalar_one())
