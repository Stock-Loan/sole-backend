from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.department import Department
from app.models.org_membership import OrgMembership
from app.schemas.departments import (
    DepartmentAssignRequest,
    DepartmentAssignResponse,
    DepartmentCreate,
    DepartmentListResponse,
    DepartmentOut,
    DepartmentUpdate,
)
from app.models.user import User
from app.schemas.users import UserListResponse
from app.services.audit import model_snapshot, record_audit_log
from app.models.user import User as UserModel
from app.models.user_role import UserRole
from app.models.role import Role

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("", response_model=DepartmentListResponse, summary="List departments")
async def list_departments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.DEPARTMENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> DepartmentListResponse:
    offset = (page - 1) * page_size
    filters = [Department.org_id == ctx.org_id]
    base = select(Department).where(*filters)
    count_stmt = select(func.count()).select_from(Department).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    result = await db.execute(base.offset(offset).limit(page_size))
    departments = result.scalars().all()

    # Member counts per department
    counts: dict[str, int] = {}
    if departments:
        dept_ids = [dept.id for dept in departments]
        count_query = (
            select(OrgMembership.department_id, func.count())
            .where(OrgMembership.org_id == ctx.org_id, OrgMembership.department_id.in_(dept_ids))
            .group_by(OrgMembership.department_id)
        )
        for dept_id, cnt in (await db.execute(count_query)).all():
            counts[str(dept_id)] = cnt

    # Attach counts
    items = []
    for dept in departments:
        dept.member_count = counts.get(str(dept.id), 0)  # type: ignore[attr-defined]
        items.append(dept)

    return DepartmentListResponse(items=items, total=total)


@router.post("", response_model=DepartmentOut, status_code=201, summary="Create a department")
async def create_department(
    payload: DepartmentCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.DEPARTMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> DepartmentOut:
    dept = Department(
        org_id=ctx.org_id,
        name=payload.name,
        code=payload.code,
        is_archived=bool(payload.is_archived),
    )
    db.add(dept)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Department name/code already exists"
        ) from exc
    await db.refresh(dept)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="department.created",
        resource_type="department",
        resource_id=str(dept.id),
        old_value=None,
        new_value=model_snapshot(dept),
    )
    await db.commit()
    return dept


@router.patch("/{department_id}", response_model=DepartmentOut, summary="Update a department")
async def update_department(
    department_id: str,
    payload: DepartmentUpdate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.DEPARTMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> DepartmentOut:
    stmt = select(Department).where(Department.id == department_id, Department.org_id == ctx.org_id)
    result = await db.execute(stmt)
    dept = result.scalar_one_or_none()
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")

    old_snapshot = model_snapshot(dept)
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"]:
        dept.name = updates["name"]
    if "code" in updates and updates["code"]:
        dept.code = updates["code"]
    if "is_archived" in updates and updates["is_archived"] is not None:
        dept.is_archived = updates["is_archived"]

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Department name/code already exists"
        ) from exc
    await db.refresh(dept)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="department.updated",
        resource_type="department",
        resource_id=str(dept.id),
        old_value=old_snapshot,
        new_value=model_snapshot(dept),
    )
    await db.commit()
    return dept


@router.delete("/{department_id}", status_code=204, summary="Delete a department")
async def delete_department(
    department_id: str,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.DEPARTMENT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    stmt = select(Department).where(Department.id == department_id, Department.org_id == ctx.org_id)
    result = await db.execute(stmt)
    dept = result.scalar_one_or_none()
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    old_snapshot = model_snapshot(dept)
    await db.delete(dept)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="department.deleted",
        resource_type="department",
        resource_id=str(dept.id),
        old_value=old_snapshot,
        new_value=None,
    )
    await db.commit()
    return None


@router.get(
    "/{department_id}/members",
    response_model=UserListResponse,
    summary="List members of a department",
)
async def list_department_members(
    department_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.DEPARTMENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    dept_stmt = select(Department).where(
        Department.id == department_id, Department.org_id == ctx.org_id
    )
    dept_result = await db.execute(dept_stmt)
    dept = dept_result.scalar_one_or_none()
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    if dept.is_archived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign members to an archived department",
        )

    offset = (page - 1) * page_size
    base_stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.department_id == dept.id)
        .order_by(UserModel.created_at)
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
        for user_role, role in (await db.execute(roles_stmt)).all():
            roles_map.setdefault(str(user_role.user_id), []).append(role)

    items = []
    for membership, user in rows:
        membership.department_name = dept.name
        items.append(
            {"user": user, "membership": membership, "roles": roles_map.get(str(user.id), [])}
        )
    return UserListResponse(items=items, total=total)


@router.post(
    "/{department_id}/assign",
    response_model=DepartmentAssignResponse,
    summary="Assign members to a department",
)
async def assign_members(
    department_id: str,
    payload: DepartmentAssignRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.DEPARTMENT_MANAGE)),
    __: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> DepartmentAssignResponse:
    dept_stmt = select(Department).where(
        Department.id == department_id, Department.org_id == ctx.org_id
    )
    dept_result = await db.execute(dept_stmt)
    dept = dept_result.scalar_one_or_none()
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")

    assigned: list[str] = []
    skipped_inactive: list[str] = []
    not_found: list[str] = []
    for membership_id in payload.membership_ids:
        mem_stmt = select(OrgMembership).where(
            OrgMembership.id == membership_id, OrgMembership.org_id == ctx.org_id
        )
        mem_result = await db.execute(mem_stmt)
        membership = mem_result.scalar_one_or_none()
        if not membership:
            not_found.append(membership_id)
            continue
        # Only assign if active
        if membership.platform_status and membership.platform_status.upper() != "ACTIVE":
            skipped_inactive.append(membership_id)
            continue
        if membership.employment_status and membership.employment_status.upper() != "ACTIVE":
            skipped_inactive.append(membership_id)
            continue
        old_snapshot = model_snapshot(membership)
        membership.department_id = dept.id
        db.add(membership)
        assigned.append(membership_id)
        record_audit_log(
            db,
            ctx,
            actor_id=current_user.id,
            action="department.member_assigned",
            resource_type="org_membership",
            resource_id=str(membership.id),
            old_value=old_snapshot,
            new_value=model_snapshot(membership),
        )
    await db.commit()
    await db.refresh(dept)
    return DepartmentAssignResponse(
        department=dept,
        assigned=assigned,
        skipped_inactive=skipped_inactive,
        not_found=not_found,
    )


@router.post("/unassign", status_code=204, summary="Unassign members from any department")
async def unassign_members(
    payload: DepartmentAssignRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.DEPARTMENT_MANAGE)),
    __: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    for membership_id in payload.membership_ids:
        mem_stmt = select(OrgMembership).where(
            OrgMembership.id == membership_id, OrgMembership.org_id == ctx.org_id
        )
        mem_result = await db.execute(mem_stmt)
        membership = mem_result.scalar_one_or_none()
        if not membership:
            continue
        old_snapshot = model_snapshot(membership)
        membership.department_id = None
        db.add(membership)
        record_audit_log(
            db,
            ctx,
            actor_id=current_user.id,
            action="department.member_unassigned",
            resource_type="org_membership",
            resource_id=str(membership.id),
            old_value=old_snapshot,
            new_value=model_snapshot(membership),
        )
    await db.commit()
    return None
