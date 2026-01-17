from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogEntry, AuditLogListResponse


router = APIRouter(prefix="/org/audit-logs", tags=["audit-logs"])


@router.get("", response_model=AuditLogListResponse, summary="List audit logs for the org")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    feature: list[str] | None = Query(default=None),
    action: list[str] | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    actor_id: UUID | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.AUDIT_LOG_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    offset = (page - 1) * page_size

    conditions = [AuditLog.org_id == ctx.org_id]
    if actor_id:
        conditions.append(AuditLog.actor_id == actor_id)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if resource_id:
        conditions.append(AuditLog.resource_id == resource_id)
    if created_from:
        conditions.append(AuditLog.created_at >= created_from)
    if created_to:
        conditions.append(AuditLog.created_at <= created_to)
    if action:
        conditions.append(AuditLog.action.in_(action))
    if feature:
        patterns = [AuditLog.action.like(f"{prefix}%") for prefix in feature]
        conditions.append(or_(*patterns))

    count_stmt = select(func.count()).select_from(AuditLog).where(*conditions)
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    stmt = (
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return AuditLogListResponse(items=[AuditLogEntry.model_validate(row) for row in rows], total=total)
