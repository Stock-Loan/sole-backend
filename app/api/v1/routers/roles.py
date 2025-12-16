import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete
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
from app.services.authz import invalidate_permission_cache

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
    # Validate permission codes
    validated_perms: list[str] = []
    for code in payload.permissions:
        try:
            validated_perms.append(PermissionCode(code).value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_permission", "message": f"Unknown permission: {code}"},
            )
    role = Role(
        org_id=ctx.org_id,
        name=payload.name,
        description=payload.description,
        is_system_role=False,
        permissions=validated_perms,
    )
    db.add(role)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role name already exists") from exc
    await db.refresh(role)
    logger.info(
        "Role created",
        extra={"org_id": ctx.org_id, "role_id": str(role.id), "name": role.name, "system": role.is_system_role},
    )
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
        # Validate permission codes
        validated = []
        for code in updates["permissions"]:
            try:
                validated.append(PermissionCode(code).value)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "invalid_permission", "message": f"Unknown permission: {code}"},
                )
        role.permissions = validated
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
    
    # Invalidate cache for all users with this role
    # This is expensive if many users have the role. 
    # Ideally, we'd use a role-based cache key or clear all caches for the org.
    # For now, we accept eventual consistency or forced re-login for updated permissions 
    # unless we iterate all users. 
    # Or better: invalidate all users in this org (simple but nuclear).
    # Optimization: iterate users with this role and invalidate them.
    user_role_stmt = select(UserRole.user_id).where(UserRole.role_id == role.id)
    user_role_result = await db.execute(user_role_stmt)
    for user_id in user_role_result.scalars().all():
        await invalidate_permission_cache(str(user_id), ctx.org_id)

    logger.info(
        "Role updated",
        extra={"org_id": ctx.org_id, "role_id": str(role.id), "name": role.name, "system": role.is_system_role},
    )
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

    # Invalidate cache for users who had this role
    user_role_stmt = select(UserRole.user_id).where(UserRole.role_id == role.id)
    user_role_result = await db.execute(user_role_stmt)
    users_to_invalidate = user_role_result.scalars().all()

    await db.delete(role)
    await db.commit()
    
    for user_id in users_to_invalidate:
        await invalidate_permission_cache(str(user_id), ctx.org_id)

    logger.info(
        "Role deleted",
        extra={"org_id": ctx.org_id, "role_id": str(role.id), "name": role.name},
    )
    return None


@router.post(
    "/org/users/{membership_id}/roles",
    response_model=list[RoleOut],
    status_code=200,
    summary="Assign roles to a user membership",
)
async def assign_roles_to_user(
    membership_id: UUID,
    payload: RoleAssignmentRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_MANAGE)),
    __: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[RoleOut]:
    if not payload.role_ids:
        return []

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

    # Validate all roles exist
    role_ids = set(payload.role_ids)
    roles_stmt = select(Role).where(Role.org_id == ctx.org_id, Role.id.in_(role_ids))
    roles_result = await db.execute(roles_stmt)
    roles = roles_result.scalars().all()
    found_ids = {r.id for r in roles}

    missing_ids = role_ids - found_ids
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "role_not_found", "message": f"Roles not found: {missing_ids}"},
        )

    # Validate constraints for each role
    for role in roles:
        if role.name != "EMPLOYEE":
            if membership.platform_status and membership.platform_status.upper() != "ACTIVE":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "membership_inactive", "message": f"Platform status must be ACTIVE for role {role.name}"},
                )
        if membership.employment_status and membership.employment_status.upper() != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "membership_inactive", "message": "Cannot assign role when employment status is not ACTIVE"},
            )

    # Identify which ones need insertion
    existing_stmt = select(UserRole.role_id).where(
        UserRole.org_id == ctx.org_id,
        UserRole.user_id == membership.user_id,
        UserRole.role_id.in_(role_ids),
    )
    existing_result = await db.execute(existing_stmt)
    existing_ids = set(existing_result.scalars().all())

    to_add = [rid for rid in role_ids if rid not in existing_ids]

    if to_add:
        db.add_all([UserRole(org_id=ctx.org_id, user_id=membership.user_id, role_id=rid) for rid in to_add])
        await db.commit()
        
        # Invalidate permission cache for this user
        await invalidate_permission_cache(str(membership.user_id), ctx.org_id)
        
        logger.info(
            "Assigned roles",
            extra={"org_id": ctx.org_id, "user_id": str(membership.user_id), "role_ids": [str(rid) for rid in to_add]},
        )
    
    return roles


@router.delete(
    "/org/users/{membership_id}/roles",
    status_code=204,
    summary="Remove roles from a user membership",
)
async def remove_roles_from_user(
    membership_id: UUID,
    payload: RoleAssignmentRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_MANAGE)),
    __: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    if not payload.role_ids:
        return None

    membership_stmt = select(OrgMembership).where(
        OrgMembership.id == membership_id,
        OrgMembership.org_id == ctx.org_id,
    )
    membership_result = await db.execute(membership_stmt)
    membership = membership_result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    stmt = delete(UserRole).where(
        UserRole.org_id == ctx.org_id,
        UserRole.user_id == membership.user_id,
        UserRole.role_id.in_(payload.role_ids),
    )
    await db.execute(stmt)
    await db.commit()
    
    # Invalidate permission cache for this user
    await invalidate_permission_cache(str(membership.user_id), ctx.org_id)
    
    logger.info(
        "Removed roles",
        extra={"org_id": ctx.org_id, "user_id": str(membership.user_id), "role_ids": [str(rid) for rid in payload.role_ids]},
    )
    return None
