import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.db.session import get_db
from app.core.permissions import PermissionCode
from app.models import User
from app.schemas.onboarding import BulkOnboardingResult, OnboardingResponse, OnboardingUserCreate
from app.schemas.users import (
    BulkDeleteRequest,
    UpdateMembershipRequest,
    UpdateUserProfileRequest,
    UserDetailResponse,
    UserListResponse,
)
from app.schemas.settings import MfaEnforcementAction
from app.models.org_membership import OrgMembership
from app.models.user import User as UserModel
from app.models.user_role import UserRole
from app.models.role import Role
from app.models.department import Department
from app.services import onboarding
from app.services.audit import model_snapshot, record_audit_log
from app.services import settings as settings_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/org/users", tags=["users"])


@router.post(
    "",
    response_model=OnboardingResponse,
    status_code=201,
    summary="Onboard a single user into the current org",
)
async def onboard_user(
    payload: OnboardingUserCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_ONBOARD)),
    db: AsyncSession = Depends(get_db),
) -> OnboardingResponse:
    try:
        user, membership, temp_password = await onboarding.onboard_single_user(db, ctx, payload)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Duplicate user or employee_id"
        ) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.onboarded",
        resource_type="org_membership",
        resource_id=str(membership.id),
        old_value=None,
        new_value={
            "user": model_snapshot(user, exclude={"hashed_password"}),
            "membership": model_snapshot(membership),
        },
    )
    await db.commit()
    return OnboardingResponse(user=user, membership=membership, temporary_password=temp_password)


@router.get(
    "/bulk/template",
    response_class=StreamingResponse,
    summary="Download CSV template for bulk onboarding",
)
async def download_template() -> StreamingResponse:
    content = onboarding.generate_csv_template()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="onboarding_template.csv"'},
    )


@router.post(
    "/bulk",
    response_model=BulkOnboardingResult,
    summary="Bulk onboard users via CSV upload",
)
async def bulk_onboard(
    file: UploadFile,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_ONBOARD)),
    db: AsyncSession = Depends(get_db),
) -> BulkOnboardingResult:
    MAX_BYTES = 5 * 1024 * 1024  # 5 MB guardrail
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="CSV too large (max 5MB)"
        )
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid encoding; expected UTF-8"
        ) from exc
    try:
        result = await onboarding.bulk_onboard_users(db, ctx, content)
        for success in result.successes:
            record_audit_log(
                db,
                ctx,
                actor_id=current_user.id,
                action="user.onboarded",
                resource_type="org_membership",
                resource_id=str(success.membership.id),
                old_value=None,
                new_value={
                    "user": model_snapshot(success.user, exclude={"hashed_password"}),
                    "membership": model_snapshot(success.membership),
                },
            )
        if result.successes:
            await db.commit()
        return result
    except onboarding.BulkOnboardCSVError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": exc.code,
                "message": str(exc),
                "data": {"successes": [], "errors": []},
                "details": exc.details,
            },
        )


@router.get("", response_model=UserListResponse, summary="List users for the current org")
async def list_users(
    page: int = 1,
    page_size: int = 20,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    offset = (page - 1) * page_size

    filters = [OrgMembership.org_id == ctx.org_id]

    base_stmt = (
        select(OrgMembership, UserModel, Department)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .where(*filters)
    )
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    result = await db.execute(base_stmt.offset(offset).limit(page_size))
    rows = result.all()
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
        items.append(
            {"user": user, "membership": membership, "roles": roles_map.get(str(user.id), [])}
        )
    return UserListResponse(items=items, total=total)


@router.get(
    "/{membership_id}", response_model=UserDetailResponse, summary="Get a user membership detail"
)
async def get_user(
    membership_id: str,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    stmt = (
        select(OrgMembership, UserModel, Department)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, dept = row
    membership.department_name = dept.name if dept else None
    roles_stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.org_id == ctx.org_id, UserRole.user_id == user.id)
    )
    roles = (await db.execute(roles_stmt)).scalars().all()
    return UserDetailResponse(user=user, membership=membership, roles=roles)


@router.delete(
    "/{membership_id}",
    status_code=204,
    summary="Delete a user membership and user if no other memberships",
)
async def delete_user(
    membership_id: str,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user = row

    membership_snapshot = model_snapshot(membership)
    user_snapshot = model_snapshot(user, exclude={"hashed_password"})
    await db.delete(membership)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.membership.deleted",
        resource_type="org_membership",
        resource_id=str(membership.id),
        old_value={"membership": membership_snapshot, "user": user_snapshot},
        new_value=None,
    )
    await db.commit()

    # If user has no other memberships, delete user record
    other_stmt = select(OrgMembership.id).where(
        OrgMembership.org_id == ctx.org_id,
        OrgMembership.user_id == user.id,
    )
    other_result = await db.execute(other_stmt)
    if not other_result.first():
        await db.delete(user)
        record_audit_log(
            db,
            ctx,
            actor_id=current_user.id,
            action="user.deleted",
            resource_type="user",
            resource_id=str(user.id),
            old_value=user_snapshot,
            new_value=None,
        )
        await db.commit()
    return None


@router.post("/bulk/delete", status_code=200, summary="Bulk delete user memberships by ID")
async def bulk_delete_users(
    payload: BulkDeleteRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    deleted = 0
    not_found: list[str] = []
    for membership_id in payload.membership_ids:
        stmt = (
            select(OrgMembership, UserModel)
            .join(UserModel, OrgMembership.user_id == UserModel.id)
            .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
        )
        result = await db.execute(stmt)
        row = result.one_or_none()
        if not row:
            not_found.append(membership_id)
            continue
        membership, user = row
        membership_snapshot = model_snapshot(membership)
        user_snapshot = model_snapshot(user, exclude={"hashed_password"})
        await db.delete(membership)
        record_audit_log(
            db,
            ctx,
            actor_id=current_user.id,
            action="user.membership.deleted",
            resource_type="org_membership",
            resource_id=str(membership.id),
            old_value={"membership": membership_snapshot, "user": user_snapshot},
            new_value=None,
        )
        await db.commit()
        other_stmt = select(OrgMembership.id).where(
            OrgMembership.org_id == ctx.org_id,
            OrgMembership.user_id == user.id,
        )
        other_result = await db.execute(other_stmt)
        if not other_result.first():
            await db.delete(user)
            record_audit_log(
                db,
                ctx,
                actor_id=current_user.id,
                action="user.deleted",
                resource_type="user",
                resource_id=str(user.id),
                old_value=user_snapshot,
                new_value=None,
            )
            await db.commit()
        deleted += 1
    return {"deleted": deleted, "not_found": not_found}


@router.patch(
    "/{membership_id}", response_model=UserDetailResponse, summary="Update membership status fields"
)
async def update_membership(
    membership_id: str,
    payload: UpdateMembershipRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
        .options(selectinload(UserModel.roles).selectinload(UserRole.role))
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user = row

    old_membership = model_snapshot(membership)
    if payload.employment_status:
        membership.employment_status = payload.employment_status
    if payload.platform_status:
        membership.platform_status = payload.platform_status

    db.add(membership)
    await db.commit()
    # Remove role assignments and revoke sessions if platform/employment status is not ACTIVE
    if (membership.platform_status and membership.platform_status.upper() != "ACTIVE") or (
        membership.employment_status and membership.employment_status.upper() != "ACTIVE"
    ):
        roles_to_remove = [ur.role for ur in user.roles if ur.org_id == ctx.org_id]
        await db.execute(
            delete(UserRole)
            .where(UserRole.org_id == ctx.org_id, UserRole.user_id == membership.user_id)
            .execution_options(synchronize_session=False)
        )
        user.token_version += 1
        db.add(user)
        await db.commit()
        for role in roles_to_remove:
            record_audit_log(
                db,
                ctx,
                actor_id=current_user.id,
                action="role.removed",
                resource_type="user_role",
                resource_id=f"{user.id}:{role.id}",
                old_value={"user_id": str(user.id), "role_id": str(role.id)},
                new_value=None,
            )
        if roles_to_remove:
            await db.commit()
        logger.info(
            "Auto-removed roles due to inactive status",
            extra={
                "org_id": ctx.org_id,
                "user_id": str(membership.user_id),
                "platform_status": membership.platform_status,
                "employment_status": membership.employment_status,
            },
        )
    await db.refresh(membership)
    await db.refresh(user)
    # Refresh logic for roles is tricky here because we just deleted them.
    # But since we eager loaded them initially, the object might still have stale roles?
    # No, we modified DB. We should expire the relationship or reload.
    await db.refresh(user, attribute_names=["roles"])
    user_roles = [ur.role for ur in user.roles if ur.org_id == ctx.org_id]
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.membership.updated",
        resource_type="org_membership",
        resource_id=str(membership.id),
        old_value=old_membership,
        new_value=model_snapshot(membership),
    )
    await db.commit()
    return UserDetailResponse(user=user, membership=membership, roles=user_roles)


@router.patch(
    "/{membership_id}/profile",
    response_model=UserDetailResponse,
    summary="Update user profile fields",
)
async def update_user_profile(
    membership_id: str,
    payload: UpdateUserProfileRequest,
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.USER_PROFILE_EDIT.value,
    )
    org_settings = await settings_service.get_org_settings(db, ctx)
    if not org_settings.allow_profile_edit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile editing is disabled for this organization",
        )
    stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
        .options(selectinload(UserModel.roles).selectinload(UserRole.role))
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user = row

    old_user = model_snapshot(user, exclude={"hashed_password"})
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    # maintain full_name if first/last change
    if payload.first_name or payload.last_name:
        first = payload.first_name if payload.first_name is not None else user.first_name or ""
        last = payload.last_name if payload.last_name is not None else user.last_name or ""
        user.full_name = f"{first} {last}".strip()

    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "duplicate_email",
                "message": "Email already exists for this organization",
                "details": {},
            },
        ) from exc
    await db.refresh(user)
    await db.refresh(membership)
    user_roles = [ur.role for ur in user.roles if ur.org_id == ctx.org_id]
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.profile.updated",
        resource_type="user",
        resource_id=str(user.id),
        old_value=old_user,
        new_value=model_snapshot(user, exclude={"hashed_password"}),
    )
    await db.commit()
    return UserDetailResponse(user=user, membership=membership, roles=user_roles)


@router.post("/{membership_id}/mfa/reset", status_code=200, summary="Admin reset of user MFA")
async def admin_reset_user_mfa(
    membership_id: str,
    request: Request,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_MFA_RESET)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Admin endpoint to reset a user's MFA. Requires USER_MFA_RESET permission.
    This action requires step-up MFA verification for the admin.
    Clears the target user's MFA settings, recovery codes, and remembered devices.
    """
    from app.services import mfa as mfa_service

    # Check if step-up MFA is required for this action
    org_settings = await settings_service.get_org_settings(db, ctx)
    if MfaEnforcementAction.USER_MFA_RESET in org_settings.mfa_required_actions:
        from app.api.auth_utils import require_step_up_mfa

        await require_step_up_mfa(request, current_user, ctx.org_id, action="USER_MFA_RESET")

    # Get the target user via membership
    stmt = (
        select(OrgMembership)
        .options(selectinload(OrgMembership.user))
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership or not membership.user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target_user = membership.user

    # Prevent resetting your own MFA via admin endpoint
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reset your own MFA via admin endpoint. Use self-service reset.",
        )

    if not target_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User does not have MFA enabled"
        )

    # Clear all MFA data
    await mfa_service.clear_user_mfa(db, org_id=ctx.org_id, user=target_user)

    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.mfa.reset",
        resource_type="user",
        resource_id=str(target_user.id),
        old_value={"mfa_enabled": True},
        new_value={"mfa_enabled": False},
    )
    await db.commit()

    return {"message": f"MFA has been reset for user {target_user.email}"}
