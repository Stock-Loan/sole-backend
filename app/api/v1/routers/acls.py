import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.access_control_list import AccessControlList
from app.models.user import User
from app.schemas.acl import ACLCreate, ACLListResponse, ACLOut
from app.services.audit import model_snapshot, record_audit_log

router = APIRouter(prefix="/acls", tags=["acls"])
logger = logging.getLogger(__name__)


@router.get("", response_model=ACLListResponse, summary="List ACL entries for current org")
async def list_acls(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ACLListResponse:
    stmt = select(AccessControlList).where(AccessControlList.org_id == ctx.org_id)
    result = await db.execute(stmt)
    items = result.scalars().all()
    return ACLListResponse(items=items)


@router.post("", response_model=ACLOut, status_code=201, summary="Create an ACL entry")
async def create_acl(
    payload: ACLCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ACLOut:
    acl = AccessControlList(
        org_id=ctx.org_id,
        user_id=payload.user_id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        permissions=payload.permissions,
    )
    db.add(acl)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ACL already exists for this user/resource",
        ) from exc
    await db.refresh(acl)
    logger.info(
        "ACL created",
        extra={
            "org_id": ctx.org_id,
            "user_id": str(payload.user_id),
            "resource_type": payload.resource_type,
            "resource_id": payload.resource_id,
        },
    )
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="acl.created",
        resource_type="acl",
        resource_id=str(acl.id),
        old_value=None,
        new_value=model_snapshot(acl),
    )
    await db.commit()
    return acl


@router.delete("/{acl_id}", status_code=204, summary="Delete an ACL entry")
async def delete_acl(
    acl_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    stmt = select(AccessControlList).where(
        AccessControlList.id == acl_id, AccessControlList.org_id == ctx.org_id
    )
    result = await db.execute(stmt)
    acl = result.scalar_one_or_none()
    if not acl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ACL not found")
    old_snapshot = model_snapshot(acl)
    await db.delete(acl)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="acl.deleted",
        resource_type="acl",
        resource_id=str(acl.id),
        old_value=old_snapshot,
        new_value=None,
    )
    await db.commit()
    logger.info(
        "ACL deleted",
        extra={
            "org_id": ctx.org_id,
            "acl_id": str(acl_id),
            "resource_type": acl.resource_type,
            "resource_id": acl.resource_id,
        },
    )
    return None
