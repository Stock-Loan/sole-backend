import csv
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.core.settings import settings
from app.db.session import get_db
from app.models.department import Department
from app.models.identity import Identity
from app.models.org import Org
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.role import Role
from app.models.user import User
from app.models.user import User as UserModel
from app.models.user_role import UserRole
from app.resources.countries import COUNTRIES, SUBDIVISIONS
from app.schemas.self import OrgSummary, RoleSummary, SelfContextResponse, SelfProfileUpdateRequest
from app.schemas.settings import MfaEnforcementAction
from app.schemas.settings import OrgPolicyResponse
from app.schemas.users import UserDetailResponse, UserSummary
from app.services.audit import model_snapshot, record_audit_log
from app.services.authz import _load_permissions_from_db
from app.services import pbgc_rates, settings as settings_service

router = APIRouter(prefix="/self", tags=["self"])

COUNTRY_NAME_BY_CODE = {entry["code"].upper(): entry["name"] for entry in COUNTRIES}
SUBDIVISION_NAME_BY_COUNTRY_AND_CODE = {
    country_code.upper(): {sub["code"].upper(): sub["name"] for sub in subdivisions}
    for country_code, subdivisions in SUBDIVISIONS.items()
}


async def _load_roles_for_user_in_org(
    db: AsyncSession,
    user_id,
    org_id: str,
) -> list[Role]:
    stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user_id,
            UserRole.org_id == org_id,
            Role.org_id == org_id,
        )
    )
    result = await db.execute(stmt)
    return result.scalars().all()


def _resolve_location_names(
    country_code: str | None, state_code: str | None
) -> tuple[str | None, str | None]:
    normalized_country = country_code.upper() if country_code else None
    normalized_state = state_code.upper() if state_code else None

    country_name = (
        COUNTRY_NAME_BY_CODE.get(normalized_country, country_code)
        if country_code
        else None
    )
    state_name = None
    if normalized_country and normalized_state:
        state_name = SUBDIVISION_NAME_BY_COUNTRY_AND_CODE.get(normalized_country, {}).get(
            normalized_state
        )
    if not state_name and state_code:
        state_name = state_code

    return country_name, state_name


def _build_user_summary_payload(user: User, profile, org_id: str, identity=None) -> dict:
    country_code = profile.country if profile else None
    state_code = profile.state if profile else None
    country_name, state_name = _resolve_location_names(country_code, state_code)

    return {
        "id": user.id,
        "org_id": org_id,
        "org_name": None,
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
        # Self profile returns display names while preserving raw codes for edit workflows.
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


async def _load_org_name(db: AsyncSession, org_id: str) -> str | None:
    stmt = select(Org.name).where(Org.id == org_id)
    return (await db.execute(stmt)).scalar_one_or_none()


@router.get(
    "/context",
    response_model=SelfContextResponse,
    summary="Get current org context, roles, and permissions",
)
async def get_self_context(
    current_user: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> SelfContextResponse:
    org_stmt = select(Org).where(Org.id == ctx.org_id)
    org_result = await db.execute(org_stmt)
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    roles = await _load_roles_for_user_in_org(db, current_user.id, ctx.org_id)

    # Effective permissions: roles + ACL/UserPermission overrides (allow/deny)
    perm_set = await _load_permissions_from_db(db, current_user.id, ctx.org_id)

    # Get org settings for session timeout
    org_settings = await settings_service.get_org_settings(db, ctx)

    return SelfContextResponse(
        org=OrgSummary.model_validate(org),
        roles=[RoleSummary.model_validate(r) for r in roles],
        permissions=sorted(perm_set),
        session_timeout_minutes=org_settings.session_timeout_minutes,
        tenancy_mode=settings.tenancy_mode,
    )


@router.get("/policy", response_model=OrgPolicyResponse, summary="Get current org policy")
async def get_self_policy(
    current_user: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> OrgPolicyResponse:
    settings = await settings_service.get_org_settings(db, ctx)
    latest_rate = await pbgc_rates.get_latest_annual_rate(db)
    response = OrgPolicyResponse.model_validate(settings)
    if latest_rate is not None:
        response = response.model_copy(update={"variable_base_rate_annual_percent": latest_rate})
    return response


@router.get("/profile", response_model=UserDetailResponse, summary="Get current user profile")
async def get_self_profile(
    current_user: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    stmt = (
        select(OrgMembership, UserModel, Department, OrgUserProfile, Identity)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Identity, Identity.id == UserModel.identity_id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == current_user.id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, dept, profile, identity = row
    membership.department_name = dept.name if dept else None
    roles = await _load_roles_for_user_in_org(db, user.id, ctx.org_id)
    org_name = await _load_org_name(db, ctx.org_id)
    user_summary = UserSummary.model_validate(_build_user_summary_payload(user, profile, ctx.org_id, identity))
    user_summary = user_summary.model_copy(update={"org_name": org_name})
    role_names = sorted({role.name for role in roles})
    return UserDetailResponse(
        user=user_summary,
        membership=membership,
        roles=roles,
        organization_name=org_name,
        role_names=role_names,
    )


@router.patch(
    "/profile",
    response_model=UserDetailResponse,
    summary="Update current user profile fields",
)
async def update_self_profile(
    payload: SelfProfileUpdateRequest,
    request: Request,
    current_user: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
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
        select(OrgMembership, UserModel, Department, OrgUserProfile, Identity)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Identity, Identity.id == UserModel.identity_id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == current_user.id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, dept, profile, identity = row
    membership.department_name = dept.name if dept else None

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        roles = await _load_roles_for_user_in_org(db, user.id, ctx.org_id)
        org_name = await _load_org_name(db, ctx.org_id)
        user_summary = UserSummary.model_validate(
            _build_user_summary_payload(user, profile, ctx.org_id, identity)
        )
        user_summary = user_summary.model_copy(update={"org_name": org_name})
        role_names = sorted({role.name for role in roles})
        return UserDetailResponse(
            user=user_summary,
            membership=membership,
            roles=roles,
            organization_name=org_name,
            role_names=role_names,
        )

    for field, value in list(updates.items()):
        if isinstance(value, str):
            cleaned = value.strip()
            updates[field] = cleaned or None

    old_profile = model_snapshot(profile) if profile else None
    if profile is None:
        profile = OrgUserProfile(
            org_id=ctx.org_id,
            membership_id=membership.id,
            full_name="",
        )
        db.add(profile)

    for field, value in updates.items():
        setattr(profile, field, value)

    db.add(profile)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.profile.self_updated",
        resource_type="org_membership",
        resource_id=str(membership.id),
        old_value={"profile": old_profile} if old_profile else None,
        new_value={"profile": model_snapshot(profile)},
    )
    await db.commit()
    await db.refresh(profile)
    await db.refresh(user)
    await db.refresh(membership)

    roles = await _load_roles_for_user_in_org(db, user.id, ctx.org_id)
    org_name = await _load_org_name(db, ctx.org_id)
    user_summary = UserSummary.model_validate(_build_user_summary_payload(user, profile, ctx.org_id, identity))
    user_summary = user_summary.model_copy(update={"org_name": org_name})
    role_names = sorted({role.name for role in roles})
    return UserDetailResponse(
        user=user_summary,
        membership=membership,
        roles=roles,
        organization_name=org_name,
        role_names=role_names,
    )


@router.get(
    "/export",
    response_class=StreamingResponse,
    summary="Export current user's personal data as CSV",
)
async def export_self_data(
    current_user: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    org_settings = await settings_service.get_org_settings(db, ctx)
    if not org_settings.allow_user_data_export:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User data export is disabled for this organization",
        )

    stmt = (
        select(OrgMembership, UserModel, Department, OrgUserProfile, Identity)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Identity, Identity.id == UserModel.identity_id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == current_user.id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, dept, profile, identity = row

    roles = await _load_roles_for_user_in_org(db, user.id, ctx.org_id)
    org_name = await _load_org_name(db, ctx.org_id)
    country_name, state_name = _resolve_location_names(
        profile.country if profile else None,
        profile.state if profile else None,
    )

    csv_headers = [
        "Field",
        "Value",
    ]
    rows = [
        ["Email", user.email or ""],
        ["First Name", profile.first_name if profile else ""],
        ["Middle Name", profile.middle_name if profile else ""],
        ["Last Name", profile.last_name if profile else ""],
        ["Preferred Name", profile.preferred_name if profile else ""],
        ["Phone Number", profile.phone_number if profile else ""],
        ["Timezone", profile.timezone if profile else ""],
        ["Marital Status", profile.marital_status if profile else ""],
        ["Country", country_name or ""],
        ["State", state_name or ""],
        ["Address Line 1", profile.address_line1 if profile else ""],
        ["Address Line 2", profile.address_line2 if profile else ""],
        ["Postal Code", profile.postal_code if profile else ""],
        ["Organization", org_name or ""],
        ["Employee ID", membership.employee_id or ""],
        ["Department", dept.name if dept else ""],
        ["Employment Status", membership.employment_status or ""],
        ["Platform Status", membership.platform_status or ""],
        ["Invitation Status", membership.invitation_status or ""],
        ["Employment Start Date", membership.employment_start_date.isoformat() if membership.employment_start_date else ""],
        ["Invited At", membership.invited_at.isoformat() if membership.invited_at else ""],
        ["Accepted At", membership.accepted_at.isoformat() if membership.accepted_at else ""],
        ["Roles", ", ".join(sorted(r.name for r in roles))],
        ["MFA Enabled", "Yes" if identity.mfa_enabled else "No"],
        ["Account Created", user.created_at.isoformat() if user.created_at else ""],
    ]

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(csv_headers)
    for row_data in rows:
        writer.writerow([str(v) if v is not None else "" for v in row_data])
    content = buffer.getvalue()

    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="user.data.exported",
        resource_type="org_membership",
        resource_id=str(membership.id),
    )
    await db.commit()

    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="my_data_export.csv"'},
    )