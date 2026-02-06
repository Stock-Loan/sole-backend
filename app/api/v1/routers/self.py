from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.core.settings import settings
from app.db.session import get_db
from app.models.department import Department
from app.models.org import Org
from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole
from app.schemas.self import OrgSummary, RoleSummary, SelfContextResponse, SelfProfileUpdateRequest
from app.schemas.settings import MfaEnforcementAction
from app.schemas.settings import OrgPolicyResponse
from app.schemas.users import UserDetailResponse, UserSummary
from app.services.audit import model_snapshot, record_audit_log
from app.services import pbgc_rates, settings as settings_service

router = APIRouter(prefix="/self", tags=["self"])


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

    roles_stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == current_user.id,
            Role.org_id == ctx.org_id,
            UserRole.org_id == ctx.org_id,
        )
    )
    roles_result = await db.execute(roles_stmt)
    roles = roles_result.scalars().all()

    # Effective permissions from roles (ACLs not included here)
    perm_set: set[str] = set()
    for role in roles:
        for code in role.permissions or []:
            try:
                perm_set.add(PermissionCode(code).value)
            except ValueError:
                continue

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
    from app.models.org_membership import OrgMembership
    from app.models.org_user_profile import OrgUserProfile
    from app.models.user import User as UserModel

    stmt = (
        select(OrgMembership, UserModel, Department, OrgUserProfile)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == current_user.id)
        .options(selectinload(UserModel.roles).selectinload(UserRole.role))
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, dept, profile = row
    await db.refresh(user, attribute_names=["roles"])
    membership.department_name = dept.name if dept else None
    roles = [ur.role for ur in user.roles if ur.org_id == ctx.org_id]
    user_summary = UserSummary.model_validate(
        {
            "id": user.id,
            "org_id": ctx.org_id,
            "email": user.email,
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "mfa_enabled": user.mfa_enabled,
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
    )
    return UserDetailResponse(user=user_summary, membership=membership, roles=roles)


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
    from app.models.org_membership import OrgMembership
    from app.models.org_user_profile import OrgUserProfile
    from app.models.user import User as UserModel

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
        select(OrgMembership, UserModel, Department, OrgUserProfile)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.membership_id == OrgMembership.id)
            & (OrgUserProfile.org_id == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == current_user.id)
        .options(selectinload(UserModel.roles).selectinload(UserRole.role))
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, dept, profile = row
    await db.refresh(user, attribute_names=["roles"])
    membership.department_name = dept.name if dept else None

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        roles = [ur.role for ur in user.roles if ur.org_id == ctx.org_id]
        user_summary = UserSummary.model_validate(
            {
                "id": user.id,
                "org_id": ctx.org_id,
                "email": user.email,
                "is_active": user.is_active,
                "is_superuser": user.is_superuser,
                "mfa_enabled": user.mfa_enabled,
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
        )
        return UserDetailResponse(user=user_summary, membership=membership, roles=roles)

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

    roles = [ur.role for ur in user.roles if ur.org_id == ctx.org_id]
    user_summary = UserSummary.model_validate(
        {
            "id": user.id,
            "org_id": ctx.org_id,
            "email": user.email,
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "mfa_enabled": user.mfa_enabled,
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
    )
    return UserDetailResponse(user=user_summary, membership=membership, roles=roles)
