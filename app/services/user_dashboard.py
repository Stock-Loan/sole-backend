from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.department import Department
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole
from app.schemas.users import UserDashboardSummary, UserDepartmentCount, UserRoleCount


async def _count_memberships(
    db: AsyncSession,
    ctx: deps.TenantContext,
) -> int:
    stmt = select(func.count()).select_from(OrgMembership).where(OrgMembership.org_id == ctx.org_id)
    return (await db.execute(stmt)).scalar_one() or 0


async def _counts_by_membership_field(
    db: AsyncSession,
    ctx: deps.TenantContext,
    field,
) -> dict[str, int]:
    stmt = (
        select(field, func.count())
        .select_from(OrgMembership)
        .where(OrgMembership.org_id == ctx.org_id)
        .group_by(field)
    )
    result = await db.execute(stmt)
    return {row[0]: row[1] for row in result.all() if row[0] is not None}


async def _count_users_with_filter(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *conditions,
) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .join(OrgMembership, OrgMembership.user_id == User.id)
        .where(OrgMembership.org_id == ctx.org_id, *conditions)
    )
    return (await db.execute(stmt)).scalar_one() or 0


async def build_dashboard_summary(
    db: AsyncSession,
    ctx: deps.TenantContext,
) -> UserDashboardSummary:
    now = datetime.now(timezone.utc)
    last_7 = now - timedelta(days=7)
    last_30 = now - timedelta(days=30)

    total_users = await _count_memberships(db, ctx)

    platform_status_counts = await _counts_by_membership_field(
        db, ctx, OrgMembership.platform_status
    )
    invitation_status_counts = await _counts_by_membership_field(
        db, ctx, OrgMembership.invitation_status
    )
    employment_status_counts = await _counts_by_membership_field(
        db, ctx, OrgMembership.employment_status
    )

    active_users = platform_status_counts.get("ACTIVE", 0)
    suspended_users = platform_status_counts.get("SUSPENDED", 0)
    invited_pending = invitation_status_counts.get("PENDING", 0)
    accepted_invites = invitation_status_counts.get("ACCEPTED", 0)

    mfa_enabled = await _count_users_with_filter(db, ctx, User.mfa_enabled.is_(True))
    mfa_disabled = max(total_users - mfa_enabled, 0)
    never_logged_in = await _count_users_with_filter(db, ctx, User.last_active_at.is_(None))
    active_last_7_days = await _count_users_with_filter(db, ctx, User.last_active_at >= last_7)
    active_last_30_days = await _count_users_with_filter(db, ctx, User.last_active_at >= last_30)
    stale_30_plus_days = await _count_users_with_filter(
        db, ctx, User.last_active_at.is_not(None), User.last_active_at < last_30
    )
    users_with_temp_password = await _count_users_with_filter(
        db, ctx, User.must_change_password.is_(True)
    )

    users_without_department_stmt = (
        select(func.count())
        .select_from(OrgMembership)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.department_id.is_(None))
    )
    users_without_department = (await db.execute(users_without_department_stmt)).scalar_one() or 0

    missing_profile_stmt = (
        select(func.count())
        .select_from(OrgMembership)
        .outerjoin(
            OrgUserProfile,
            (OrgUserProfile.org_id == OrgMembership.org_id)
            & (OrgUserProfile.membership_id == OrgMembership.id),
        )
        .where(
            OrgMembership.org_id == ctx.org_id,
            (
                OrgUserProfile.id.is_(None)
                | OrgUserProfile.timezone.is_(None)
                | OrgUserProfile.phone_number.is_(None)
                | OrgUserProfile.address_line1.is_(None)
                | OrgUserProfile.country.is_(None)
            ),
        )
    )
    missing_profile_fields = (await db.execute(missing_profile_stmt)).scalar_one() or 0

    department_stmt = (
        select(Department.id, Department.name, func.count(OrgMembership.id))
        .select_from(Department)
        .outerjoin(
            OrgMembership,
            (OrgMembership.department_id == Department.id) & (OrgMembership.org_id == ctx.org_id),
        )
        .where(Department.org_id == ctx.org_id, Department.is_archived.is_(False))
        .group_by(Department.id, Department.name)
        .order_by(Department.name.asc())
    )
    department_rows = (await db.execute(department_stmt)).all()
    department_counts = [
        UserDepartmentCount(department_id=row[0], department_name=row[1], count=row[2])
        for row in department_rows
    ]

    role_stmt = (
        select(Role.id, Role.name, func.count(UserRole.user_id))
        .select_from(Role)
        .outerjoin(
            UserRole,
            (UserRole.role_id == Role.id) & (UserRole.org_id == ctx.org_id),
        )
        .where(Role.org_id == ctx.org_id)
        .group_by(Role.id, Role.name)
        .order_by(Role.name.asc())
    )
    role_rows = (await db.execute(role_stmt)).all()
    role_counts = [
        UserRoleCount(role_id=row[0], role_name=row[1], count=row[2]) for row in role_rows
    ]
    roles_with_zero_members = [role.role_name for role in role_counts if role.count == 0]

    return UserDashboardSummary(
        org_id=ctx.org_id,
        total_users=total_users,
        platform_status_counts=platform_status_counts,
        invitation_status_counts=invitation_status_counts,
        employment_status_counts=employment_status_counts,
        active_users=active_users,
        suspended_users=suspended_users,
        invited_pending=invited_pending,
        accepted_invites=accepted_invites,
        mfa_enabled=mfa_enabled,
        mfa_disabled=mfa_disabled,
        never_logged_in=never_logged_in,
        active_last_7_days=active_last_7_days,
        active_last_30_days=active_last_30_days,
        stale_30_plus_days=stale_30_plus_days,
        users_with_temp_password=users_with_temp_password,
        users_without_department=users_without_department,
        missing_profile_fields=missing_profile_fields,
        department_counts=department_counts,
        role_counts=role_counts,
        roles_with_zero_members=roles_with_zero_members,
    )
