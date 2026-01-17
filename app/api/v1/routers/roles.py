import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, delete, func
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
from app.schemas.users import UserListResponse
from app.models.department import Department
from app.services.authz import invalidate_permission_cache

router = APIRouter(prefix="/roles", tags=["roles"])
logger = logging.getLogger(__name__)


@router.get("", response_model=RoleListResponse, summary="List roles for current org")
async def list_roles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> RoleListResponse:
    offset = (page - 1) * page_size
    stmt = select(Role).where(Role.org_id == ctx.org_id)
    count_stmt = select(func.count()).select_from(Role).where(Role.org_id == ctx.org_id)
    total = (await db.execute(count_stmt)).scalar_one()
    result = await db.execute(stmt.offset(offset).limit(page_size))
    roles = result.scalars().all()
    return RoleListResponse(items=roles, total=total)


@router.get("/{role_id}/members", response_model=UserListResponse, summary="List members assigned to a role")
async def list_role_members(
    role_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.ROLE_VIEW)),
    __: User = Depends(deps.require_permission(PermissionCode.USER_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    role_stmt = select(Role).where(Role.id == role_id, Role.org_id == ctx.org_id)
    role_result = await db.execute(role_stmt)
    role = role_result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    offset = (page - 1) * page_size
    base_stmt = (
        select(OrgMembership, User, Department)
        .join(User, OrgMembership.user_id == User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .where(
            OrgMembership.org_id == ctx.org_id,
            UserRole.org_id == ctx.org_id,
            UserRole.role_id == role_id,
        )
        .order_by(User.created_at)
    )
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    rows = (await db.execute(base_stmt.offset(offset).limit(page_size))).all()
    user_ids = [row[1].id for row in rows]
    roles_map: dict[str, list[Role]] = {}
    if user_ids:
        roles_stmt = (
            select(UserRole, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.org_id == ctx.org_id, UserRole.user_id.in_(user_ids))
        )
        roles_result = await db.execute(roles_stmt)
        for user_role, role in roles_result.all():
            roles_map.setdefault(str(user_role.user_id), []).append(role)

    items = []
    for membership, user, dept in rows:
        membership.department_name = dept.name if dept else None
        items.append({"user": user, "membership": membership, "roles": roles_map.get(str(user.id), [])})
    return UserListResponse(items=items, total=total)


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
    # Optimization: Perform checks once if possible, but role names might differ
    # We can check platform/employment status once
    platform_active = membership.platform_status and membership.platform_status.upper() == "ACTIVE"
    employment_active = membership.employment_status and membership.employment_status.upper() == "ACTIVE"

    if not employment_active:
         raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "membership_inactive", "message": "Cannot assign role when employment status is not ACTIVE"},
        )

    for role in roles:
        if role.name != "EMPLOYEE" and not platform_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "membership_inactive", "message": f"Platform status must be ACTIVE for role {role.name}"},
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


@router.post(
    "/org/users/{membership_id}/permissions/invalidate",
    status_code=204,
    summary="Invalidate permission cache for a user membership",
)
async def invalidate_user_permissions(
    membership_id: UUID,
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

    await invalidate_permission_cache(str(membership.user_id), ctx.org_id)
    logger.info(
        "Invalidated permission cache",
        extra={"org_id": ctx.org_id, "user_id": str(membership.user_id)},
    )
    return None
