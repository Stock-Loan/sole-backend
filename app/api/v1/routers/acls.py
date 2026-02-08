import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.access_control_list import AccessControlList
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.models.user_permission import UserPermission
from app.schemas.acl import ACLCreate, ACLListResponse, ACLOut, ACLUpdate
from app.schemas.user_permissions import (
    UserPermissionAssignmentCreate,
    UserPermissionAssignmentList,
    UserPermissionAssignmentOut,
    UserPermissionAssignmentUpdate,
)
from app.services.audit import model_snapshot, record_audit_log
from app.services.authz import invalidate_permission_cache
from app.schemas.settings import MfaEnforcementAction

router = APIRouter(prefix="/acls", tags=["acls"])
logger = logging.getLogger(__name__)


async def _require_membership(db: AsyncSession, *, org_id: str, user_id: UUID) -> None:
    stmt = select(OrgMembership.id).where(
        OrgMembership.org_id == org_id,
        OrgMembership.user_id == user_id,
    )
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of this organization",
        )


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
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ACLOut:
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.ACL_ASSIGNMENT.value,
    )
    await _require_membership(db, org_id=ctx.org_id, user_id=payload.user_id)
    acl = AccessControlList(
        org_id=ctx.org_id,
        user_id=payload.user_id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        permissions=payload.permissions,
        effect=payload.effect,
        expires_at=payload.expires_at,
    )
    db.add(acl)
    try:
        await db.flush()
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


@router.patch("/{acl_id}", response_model=ACLOut, summary="Update an ACL entry")
async def update_acl(
    acl_id: UUID,
    payload: ACLUpdate,
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ACLOut:
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.ACL_ASSIGNMENT.value,
    )
    stmt = select(AccessControlList).where(
        AccessControlList.id == acl_id, AccessControlList.org_id == ctx.org_id
    )
    result = await db.execute(stmt)
    acl = result.scalar_one_or_none()
    if not acl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ACL not found")
    old_snapshot = model_snapshot(acl)
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(acl, field, value)
    await db.flush()
    await db.refresh(acl)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="acl.updated",
        resource_type="acl",
        resource_id=str(acl.id),
        old_value=old_snapshot,
        new_value=model_snapshot(acl),
    )
    await db.commit()
    return acl


@router.delete("/{acl_id}", status_code=204, summary="Delete an ACL entry")
async def delete_acl(
    acl_id: UUID,
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.ACL_ASSIGNMENT.value,
    )
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


@router.get(
    "/assignments",
    response_model=UserPermissionAssignmentList,
    summary="List direct permission assignments for the org",
)
async def list_user_permission_assignments(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> UserPermissionAssignmentList:
    stmt = (
        select(UserPermission, OrgUserProfile.full_name, User.email)
        .join(User, User.id == UserPermission.user_id)
        .join(
            OrgMembership,
            (OrgMembership.user_id == User.id) & (OrgMembership.org_id == ctx.org_id),
        )
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.org_id == OrgMembership.org_id)
            & (OrgUserProfile.membership_id == OrgMembership.id),
        )
        .where(UserPermission.org_id == ctx.org_id)
        .order_by(OrgUserProfile.full_name.asc())
    )
    result = await db.execute(stmt)
    items: list[UserPermissionAssignmentOut] = []
    for row in result.all():
        assignment, full_name, email = row
        items.append(
            UserPermissionAssignmentOut(
                id=assignment.id,
                org_id=assignment.org_id,
                user_id=assignment.user_id,
                full_name=full_name,
                email=email,
                permissions=assignment.permissions,
                effect=assignment.effect,
                expires_at=assignment.expires_at,
                created_at=assignment.created_at,
                updated_at=assignment.updated_at,
            )
        )
    return UserPermissionAssignmentList(items=items)


@router.post(
    "/assignments",
    response_model=UserPermissionAssignmentOut,
    status_code=201,
    summary="Create a direct permission assignment for a user",
)
async def create_user_permission_assignment(
    payload: UserPermissionAssignmentCreate,
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> UserPermissionAssignmentOut:
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.ACL_ASSIGNMENT.value,
    )
    await _require_membership(db, org_id=ctx.org_id, user_id=payload.user_id)
    assignment = UserPermission(
        org_id=ctx.org_id,
        user_id=payload.user_id,
        permissions=payload.permissions,
        effect=payload.effect,
        expires_at=payload.expires_at,
    )
    db.add(assignment)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Direct permission assignment already exists for this user",
        ) from exc
    await db.refresh(assignment)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user_permissions.created",
        resource_type="user_permissions",
        resource_id=str(assignment.user_id),
        old_value=None,
        new_value=model_snapshot(assignment),
    )
    await db.commit()
    await invalidate_permission_cache(str(assignment.user_id), ctx.org_id)
    user_row = await db.execute(
        select(OrgUserProfile.full_name, User.email)
        .select_from(User)
        .join(
            OrgMembership,
            (OrgMembership.user_id == User.id) & (OrgMembership.org_id == ctx.org_id),
        )
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.org_id == OrgMembership.org_id)
            & (OrgUserProfile.membership_id == OrgMembership.id),
        )
        .where(User.id == assignment.user_id)
    )
    full_name, email = user_row.one()
    return UserPermissionAssignmentOut(
        id=assignment.id,
        org_id=assignment.org_id,
        user_id=assignment.user_id,
        full_name=full_name,
        email=email,
        permissions=assignment.permissions,
        effect=assignment.effect,
        expires_at=assignment.expires_at,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


@router.patch(
    "/assignments/{assignment_id}",
    response_model=UserPermissionAssignmentOut,
    summary="Update a direct permission assignment",
)
async def update_user_permission_assignment(
    assignment_id: UUID,
    payload: UserPermissionAssignmentUpdate,
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> UserPermissionAssignmentOut:
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.ACL_ASSIGNMENT.value,
    )
    stmt = select(UserPermission).where(
        UserPermission.id == assignment_id, UserPermission.org_id == ctx.org_id
    )
    assignment = (await db.execute(stmt)).scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    old_snapshot = model_snapshot(assignment)
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(assignment, field, value)
    await db.flush()
    await db.refresh(assignment)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user_permissions.updated",
        resource_type="user_permissions",
        resource_id=str(assignment.user_id),
        old_value=old_snapshot,
        new_value=model_snapshot(assignment),
    )
    await db.commit()
    await invalidate_permission_cache(str(assignment.user_id), ctx.org_id)
    user_row = await db.execute(
        select(OrgUserProfile.full_name, User.email)
        .select_from(User)
        .join(
            OrgMembership,
            (OrgMembership.user_id == User.id) & (OrgMembership.org_id == ctx.org_id),
        )
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.org_id == OrgMembership.org_id)
            & (OrgUserProfile.membership_id == OrgMembership.id),
        )
        .where(User.id == assignment.user_id)
    )
    full_name, email = user_row.one()
    return UserPermissionAssignmentOut(
        id=assignment.id,
        org_id=assignment.org_id,
        user_id=assignment.user_id,
        full_name=full_name,
        email=email,
        permissions=assignment.permissions,
        effect=assignment.effect,
        expires_at=assignment.expires_at,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


@router.delete(
    "/assignments/{assignment_id}",
    status_code=204,
    summary="Delete a direct permission assignment",
)
async def delete_user_permission_assignment(
    assignment_id: UUID,
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.ACL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.ACL_ASSIGNMENT.value,
    )
    stmt = select(UserPermission).where(
        UserPermission.id == assignment_id, UserPermission.org_id == ctx.org_id
    )
    assignment = (await db.execute(stmt)).scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    old_snapshot = model_snapshot(assignment)
    await db.delete(assignment)
    await db.flush()
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user_permissions.deleted",
        resource_type="user_permissions",
        resource_id=str(assignment.user_id),
        old_value=old_snapshot,
        new_value=None,
    )
    await db.commit()
    await invalidate_permission_cache(str(assignment.user_id), ctx.org_id)
    return None
