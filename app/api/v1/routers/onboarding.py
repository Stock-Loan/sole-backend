import logging

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.db.session import get_db
from app.core.permissions import PermissionCode
from app.models import User
from app.models.identity import Identity
from app.schemas.onboarding import (
    BulkOnboardingResult,
    OnboardingResponse,
    OnboardingUserCreate,
    OnboardingUserOut,
)
from app.schemas.users import (
    BulkDeleteRequest,
    UpdateMembershipRequest,
    UpdateUserProfileRequest,
    UserDetailResponse,
    UserListResponse,
    UserSummary,
)
from app.schemas.settings import MfaEnforcementAction
from app.models.org_membership import OrgMembership
from app.models.org import Org
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User as UserModel
from app.models.user_role import UserRole
from app.models.role import Role
from app.models.department import Department
from app.resources.countries import COUNTRIES, SUBDIVISIONS
from app.services import onboarding
from app.services import authz
from app.services.audit import model_snapshot, record_audit_log
from app.services import settings as settings_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/org/users", tags=["users"])

COUNTRY_NAME_BY_CODE = {entry["code"].upper(): entry["name"] for entry in COUNTRIES}
SUBDIVISION_NAME_BY_COUNTRY_AND_CODE = {
    country_code.upper(): {sub["code"].upper(): sub["name"] for sub in subdivisions}
    for country_code, subdivisions in SUBDIVISIONS.items()
}


def _resolve_location_names(
    country_code: str | None, state_code: str | None
) -> tuple[str | None, str | None]:
    normalized_country = country_code.upper() if country_code else None
    normalized_state = state_code.upper() if state_code else None

    country_name = (
        COUNTRY_NAME_BY_CODE.get(normalized_country, country_code) if country_code else None
    )
    state_name = None
    if normalized_country and normalized_state:
        state_name = SUBDIVISION_NAME_BY_COUNTRY_AND_CODE.get(normalized_country, {}).get(
            normalized_state
        )
    if not state_name and state_code:
        state_name = state_code

    return country_name, state_name


async def _load_org_name(db: AsyncSession, org_id: str) -> str | None:
    stmt = select(Org.name).where(Org.id == org_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _load_roles_for_user_in_org(
    db: AsyncSession,
    user_id,
    org_id: str,
) -> list[Role]:
    stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.org_id == org_id, UserRole.user_id == user_id, Role.org_id == org_id)
    )
    return (await db.execute(stmt)).scalars().all()


def _user_summary(
    user: UserModel,
    profile: OrgUserProfile | None,
    *,
    org_id: str,
    org_name: str | None = None,
    identity=None,
) -> UserSummary:
    country_code = profile.country if profile else None
    state_code = profile.state if profile else None
    country_name, state_name = _resolve_location_names(country_code, state_code)
    data = {
        "id": user.id,
        "org_id": org_id,
        "org_name": org_name,
        "email": user.email,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "mfa_enabled": identity.mfa_enabled if identity else False,
        "created_at": user.created_at,
        "first_name": profile.first_name if profile else None,
        "middle_name": profile.middle_name if profile else None,
        "last_name": profile.last_name if profile else None,
        "preferred_name": profile.preferred_name if profile else None,
        "timezone": profile.timezone if profile else None,
        "phone_number": profile.phone_number if profile else None,
        "marital_status": profile.marital_status if profile else None,
        # Keep display values and raw codes together for frontend table + edit flows.
        "country": country_name,
        "state": state_name,
        "country_code": country_code,
        "state_code": state_code,
        "country_name": country_name,
        "state_name": state_name,
        "address_line1": profile.address_line1 if profile else None,
        "address_line2": profile.address_line2 if profile else None,
        "postal_code": profile.postal_code if profile else None,
    }
    return UserSummary.model_validate(data)


def _onboarding_user(
    user: UserModel, profile: OrgUserProfile | None, *, org_id: str
) -> OnboardingUserOut:
    data = {
        "id": user.id,
        "org_id": org_id,
        "email": user.email,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "created_at": user.created_at,
        "first_name": profile.first_name if profile else None,
        "middle_name": profile.middle_name if profile else None,
        "last_name": profile.last_name if profile else None,
        "preferred_name": profile.preferred_name if profile else None,
        "timezone": profile.timezone if profile else None,
        "phone_number": profile.phone_number if profile else None,
        "marital_status": profile.marital_status if profile else None,
        "country": profile.country if profile else None,
        "state": profile.state if profile else None,
        "address_line1": profile.address_line1 if profile else None,
        "address_line2": profile.address_line2 if profile else None,
        "postal_code": profile.postal_code if profile else None,
    }
    return OnboardingUserOut.model_validate(data)


@router.post(
    "",
    response_model=OnboardingResponse,
    status_code=201,
    summary="Onboard a single user into the current org",
)
async def onboard_user(
    payload: OnboardingUserCreate,
    response: Response,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_ONBOARD)),
    db: AsyncSession = Depends(get_db),
) -> OnboardingResponse:
    try:
        result = await onboarding.onboard_single_user(db, ctx, payload)
    except IntegrityError as exc:
        await db.rollback()
        message = onboarding.describe_integrity_error(exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "constraint_error",
                "message": message,
                "details": onboarding.integrity_error_details(exc),
            },
        ) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if result.membership_status == "created":
        record_audit_log(
            db,
            ctx,
            actor_id=current_user.id,
            action="user.onboarded",
            resource_type="org_membership",
            resource_id=str(result.membership.id),
            old_value=None,
            new_value={
                "user": model_snapshot(result.user, exclude={"hashed_password"}),
                "membership": model_snapshot(result.membership),
            },
        )
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return OnboardingResponse(
        user=_onboarding_user(result.user, result.profile, org_id=ctx.org_id),
        membership=result.membership,
        user_status=result.user_status,
        membership_status=result.membership_status,
        credentials_issued=bool(result.temporary_password),
    )


@router.get(
    "/bulk/template",
    response_class=StreamingResponse,
    summary="Download CSV template for bulk onboarding",
)
async def download_template(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_ONBOARD)),
) -> StreamingResponse:
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
    response: Response,
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
            if success.membership_status != "created":
                continue
            user_snapshot = (
                model_snapshot(success.user, exclude={"hashed_password"})
                if hasattr(success.user, "__table__")
                else success.user.model_dump()
            )
            membership_snapshot = (
                model_snapshot(success.membership)
                if hasattr(success.membership, "__table__")
                else success.membership.model_dump()
            )
            record_audit_log(
                db,
                ctx,
                actor_id=current_user.id,
                action="user.onboarded",
                resource_type="org_membership",
                resource_id=str(success.membership.id),
                old_value=None,
                new_value={
                    "user": user_snapshot,
                    "membership": membership_snapshot,
                },
            )
        if result.successes:
            await db.commit()
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
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
        select(OrgMembership, UserModel, Department, OrgUserProfile, Identity)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Identity, Identity.id == UserModel.identity_id)
        .outerjoin(Department, OrgMembership.department_id == Department.id)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(*filters)
    )
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    result = await db.execute(base_stmt.offset(offset).limit(page_size))
    rows = result.all()
    org_name = await _load_org_name(db, ctx.org_id)
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
    for membership, user, dept, profile, identity in rows:
        membership.department_name = dept.name if dept else None
        items.append(
            {
                "user": _user_summary(
                    user,
                    profile,
                    org_id=ctx.org_id,
                    org_name=org_name,
                    identity=identity,
                ),
                "membership": membership,
                "roles": roles_map.get(str(user.id), []),
            }
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
        select(OrgMembership, UserModel, Department, OrgUserProfile, Identity)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Identity, Identity.id == UserModel.identity_id)
        .outerjoin(Department, OrgMembership.department_id == Department.id)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, dept, profile, identity = row
    membership.department_name = dept.name if dept else None
    roles_stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.org_id == ctx.org_id, UserRole.user_id == user.id)
    )
    roles = (await db.execute(roles_stmt)).scalars().all()
    org_name = await _load_org_name(db, ctx.org_id)
    return UserDetailResponse(
        user=_user_summary(
            user,
            profile,
            org_id=ctx.org_id,
            org_name=org_name,
            identity=identity,
        ),
        membership=membership,
        roles=roles,
        organization_name=org_name,
        role_names=sorted({role.name for role in roles}),
    )


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
        select(OrgMembership, UserModel, OrgUserProfile)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, profile = row

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
    await db.flush()

    # If user has no other memberships, delete user record
    other_stmt = select(OrgMembership.id).where(OrgMembership.user_id == user.id)
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
        await db.flush()
        other_stmt = select(OrgMembership.id).where(OrgMembership.user_id == user.id)
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
        select(OrgMembership, UserModel, OrgUserProfile, Identity)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Identity, Identity.id == UserModel.identity_id)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, profile, identity = row

    old_membership = model_snapshot(membership)
    if payload.employment_status:
        membership.employment_status = payload.employment_status
    if payload.platform_status:
        membership.platform_status = payload.platform_status

    db.add(membership)
    await db.flush()
    # Remove role assignments and revoke sessions if platform/employment status is not ACTIVE
    _invalidate_user_id = None
    if (membership.platform_status and membership.platform_status.upper() != "ACTIVE") or (
        membership.employment_status and membership.employment_status.upper() != "ACTIVE"
    ):
        roles_to_remove = await _load_roles_for_user_in_org(db, user.id, ctx.org_id)
        await db.execute(
            delete(UserRole)
            .where(UserRole.org_id == ctx.org_id, UserRole.user_id == membership.user_id)
            .execution_options(synchronize_session=False)
        )
        identity.token_version += 1
        db.add(identity)
        await db.flush()
        _invalidate_user_id = str(membership.user_id)
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
    user_roles = await _load_roles_for_user_in_org(db, user.id, ctx.org_id)
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
    if _invalidate_user_id:
        await authz.invalidate_permission_cache(_invalidate_user_id, ctx.org_id)
    org_name = await _load_org_name(db, ctx.org_id)
    return UserDetailResponse(
        user=_user_summary(
            user,
            profile,
            org_id=ctx.org_id,
            org_name=org_name,
            identity=identity,
        ),
        membership=membership,
        roles=user_roles,
        organization_name=org_name,
        role_names=sorted({role.name for role in user_roles}),
    )


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
    # Note: allow_profile_edit only gates self-service edits (PATCH /self/profile).
    # Admins with user.manage can always update user profiles.
    stmt = (
        select(OrgMembership, UserModel, OrgUserProfile, Identity)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Identity, Identity.id == UserModel.identity_id)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, profile, identity = row

    old_user = model_snapshot(user, exclude={"hashed_password"})
    old_profile = model_snapshot(profile) if profile else None

    updates = payload.model_dump(exclude_unset=True)
    if "email" in updates:
        user.email = updates.pop("email")

    if profile is None:
        profile = OrgUserProfile(
            org_id=ctx.org_id,
            membership_id=membership.id,
            full_name="",
        )
        db.add(profile)

    for field, value in updates.items():
        setattr(profile, field, value)

    if "first_name" in updates or "last_name" in updates:
        first = profile.first_name or ""
        last = profile.last_name or ""
        profile.full_name = f"{first} {last}".strip()

    db.add(user)
    db.add(profile)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "duplicate_email",
                "message": "Email already exists",
                "details": {},
            },
        ) from exc
    await db.refresh(user)
    await db.refresh(membership)
    await db.refresh(profile)
    user_roles = await _load_roles_for_user_in_org(db, user.id, ctx.org_id)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.profile.updated",
        resource_type="user",
        resource_id=str(user.id),
        old_value={"user": old_user, "profile": old_profile},
        new_value={
            "user": model_snapshot(user, exclude={"hashed_password"}),
            "profile": model_snapshot(profile) if profile else None,
        },
    )
    await db.commit()
    org_name = await _load_org_name(db, ctx.org_id)
    return UserDetailResponse(
        user=_user_summary(
            user,
            profile,
            org_id=ctx.org_id,
            org_name=org_name,
            identity=identity,
        ),
        membership=membership,
        roles=user_roles,
        organization_name=org_name,
        role_names=sorted({role.name for role in user_roles}),
    )


@router.post(
    "/{membership_id}/force-password-reset",
    status_code=200,
    summary="Force password reset for a user",
)
async def force_password_reset(
    membership_id: str,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    current_user: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Admin endpoint to force a password reset for a user. Requires USER_MANAGE permission.
    Sets must_change_password=True so the user is prompted to change their password
    on next login. Invalidates existing sessions by bumping token_version.
    The user's current password remains valid for login.
    """
    stmt = (
        select(OrgMembership)
        .options(
            selectinload(OrgMembership.user).selectinload(UserModel.identity)
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership or not membership.user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target_user = membership.user
    target_identity = target_user.identity

    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot force-reset your own password. Use change-password instead.",
        )

    if not target_identity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User has no identity record"
        )

    target_identity.must_change_password = True
    target_identity.token_version += 1
    db.add(target_identity)

    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.password.force_reset",
        resource_type="user",
        resource_id=str(target_user.id),
    )
    await db.commit()

    return {"message": f"Password reset required for {target_user.email}"}


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
        .options(
            selectinload(OrgMembership.user).selectinload(UserModel.identity)
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership or not membership.user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target_user = membership.user
    target_identity = target_user.identity

    # Prevent resetting your own MFA via admin endpoint
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reset your own MFA via admin endpoint. Use self-service reset.",
        )

    if not target_identity or not target_identity.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User does not have MFA enabled"
        )

    # Clear all MFA data
    await mfa_service.clear_user_mfa(db, target_identity, org_id=ctx.org_id, user_id=target_user.id)

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
