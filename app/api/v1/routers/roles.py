import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.org_membership import OrgMembership
from app.models.role import Role
from app.models.user_role import UserRole
from app.models.user import User
from app.schemas.roles import RoleAssignmentRequest, RoleCreate, RoleListResponse, RoleOut, RoleUpdate

router = APIRouter(prefix="/roles", tags=["roles"])
logger = logging.getLogger(__name__)


@router.get("", response_model=RoleListResponse, summary="List roles for current org")
async def list_roles(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> RoleListResponse:
    stmt = select(Role).where(Role.org_id == ctx.org_id).order_by(Role.name)
    result = await db.execute(stmt)
    roles = result.scalars().all()
    return RoleListResponse(items=roles)


@router.post("", response_model=RoleOut, status_code=201, summary="Create a custom role")
async def create_role(
    payload: RoleCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    role = Role(
        org_id=ctx.org_id,
        name=payload.name,
        description=payload.description,
        is_system_role=False,
        permissions=payload.permissions,
    )
    db.add(role)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role name already exists") from exc
    await db.refresh(role)
    return role


@router.patch("/{role_id}", response_model=RoleOut, summary="Update a custom role")
async def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    stmt = select(Role).where(Role.id == role_id, Role.org_id == ctx.org_id)
    result = await db.execute(stmt)
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if role.is_system_role:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="System roles cannot be edited")

    updates = payload.model_dump(exclude_unset=True)
    if "permissions" in updates and updates["permissions"] is not None:
        role.permissions = updates["permissions"]
    if "name" in updates and updates["name"]:
        role.name = updates["name"]
    if "description" in updates:
        role.description = updates["description"]

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role name already exists") from exc
    await db.refresh(role)
    return role


@router.delete("/{role_id}", status_code=204, summary="Delete a custom role")
async def delete_role(
    role_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    stmt = select(Role).where(Role.id == role_id, Role.org_id == ctx.org_id)
    result = await db.execute(stmt)
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if role.is_system_role:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="System roles cannot be deleted")

    await db.delete(role)
    await db.commit()
    return None


@router.post(
    "/org/users/{membership_id}/roles",
    response_model=RoleOut,
    status_code=200,
    summary="Assign a role to a user membership",
)
async def assign_role_to_user(
    membership_id: UUID,
    payload: RoleAssignmentRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_MANAGE)),
    __: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    membership_stmt = select(OrgMembership).where(
        OrgMembership.id == membership_id,
        OrgMembership.org_id == ctx.org_id,
    )
    membership_result = await db.execute(membership_stmt)
    membership = membership_result.scalar_one_or_none()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "membership_not_found", "message": "Membership not found"},
        )
    user_stmt = select(User).where(User.id == membership.user_id, User.org_id == ctx.org_id)
    user_result = await db.execute(user_stmt)
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "user_inactive", "message": "Cannot assign role to inactive user"},
        )
    if membership.employment_status and membership.employment_status.upper() != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "membership_inactive", "message": "Cannot assign role when employment status is not ACTIVE"},
        )

    role_stmt = select(Role).where(Role.id == payload.role_id, Role.org_id == ctx.org_id)
    role_result = await db.execute(role_stmt)
    role = role_result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "role_not_found", "message": "Role not found"},
        )

    link_stmt = select(UserRole).where(
        UserRole.org_id == ctx.org_id,
        UserRole.user_id == membership.user_id,
        UserRole.role_id == role.id,
    )
    link_result = await db.execute(link_stmt)
    existing = link_result.scalar_one_or_none()
    if not existing:
        db.add(UserRole(org_id=ctx.org_id, user_id=membership.user_id, role_id=role.id))
        await db.commit()
        logger.info("Assigned role", extra={"org_id": ctx.org_id, "user_id": str(membership.user_id), "role_id": str(role.id)})
    return role


@router.delete(
    "/org/users/{membership_id}/roles/{role_id}",
    status_code=204,
    summary="Remove a role from a user membership",
)
async def remove_role_from_user(
    membership_id: UUID,
    role_id: UUID,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_MANAGE)),
    __: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    membership_stmt = select(OrgMembership).where(
        OrgMembership.id == membership_id,
        OrgMembership.org_id == ctx.org_id,
    )
    membership_result = await db.execute(membership_stmt)
    membership = membership_result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    link_stmt = select(UserRole).where(
        UserRole.org_id == ctx.org_id,
        UserRole.user_id == membership.user_id,
        UserRole.role_id == role_id,
    )
    link_result = await db.execute(link_stmt)
    link = link_result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role assignment not found")

    await db.delete(link)
    await db.commit()
    return None
