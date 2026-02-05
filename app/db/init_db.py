import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.core.settings import settings
from app.db.session import AsyncSessionLocal
from app.models.org import Org
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.services.authz import ensure_user_in_role, seed_system_roles
from app.services.orgs import ensure_audit_partitions_for_orgs


def _parse_seed_org_ids() -> list[str]:
    org_ids = [settings.default_org_id]
    if settings.extra_seed_org_ids:
        extras = [oid.strip() for oid in settings.extra_seed_org_ids.split(",") if oid.strip()]
        org_ids.extend(extras)
    # Preserve order but ensure uniqueness
    seen: set[str] = set()
    ordered: list[str] = []
    for oid in org_ids:
        if oid in seen:
            continue
        seen.add(oid)
        ordered.append(oid)
    return ordered


def _org_name_for_seed(org_id: str) -> str:
    if org_id == settings.default_org_id:
        return settings.default_org_name
    return f"{org_id.replace('-', ' ').title()} Organization"


def _org_slug_for_seed(org_id: str) -> str:
    if org_id == settings.default_org_id:
        return settings.default_org_slug or settings.default_org_id
    return org_id


def _is_production() -> bool:
    return settings.environment.lower() in {"production", "prod"}


async def _ensure_org(session: AsyncSession, org_id: str) -> Org:
    org_stmt = select(Org).where(Org.id == org_id)
    org_result = await session.execute(org_stmt)
    org = org_result.scalar_one_or_none()
    if org:
        return org
    org = Org(
        id=org_id,
        name=_org_name_for_seed(org_id),
        slug=_org_slug_for_seed(org_id),
        status="ACTIVE",
    )
    session.add(org)
    await session.commit()
    return org


async def _ensure_membership_and_profile(
    session: AsyncSession,
    *,
    org_id: str,
    user: User,
    employee_id: str,
    full_name: str,
    first_name: str,
    last_name: str,
) -> OrgMembership:
    mem_stmt = select(OrgMembership).where(
        OrgMembership.org_id == org_id, OrgMembership.user_id == user.id
    )
    mem_result = await session.execute(mem_stmt)
    membership = mem_result.scalar_one_or_none()
    if not membership:
        now = datetime.now(timezone.utc)
        membership = OrgMembership(
            org_id=org_id,
            user_id=user.id,
            employee_id=employee_id,
            employment_status="ACTIVE",
            platform_status="ACTIVE",
            invitation_status="ACCEPTED",
            invited_at=now,
            accepted_at=now,
        )
        session.add(membership)
        await session.commit()

    profile_stmt = select(OrgUserProfile).where(
        OrgUserProfile.org_id == org_id,
        OrgUserProfile.membership_id == membership.id,
    )
    profile = (await session.execute(profile_stmt)).scalar_one_or_none()
    if not profile:
        profile = OrgUserProfile(
            org_id=org_id,
            membership_id=membership.id,
            full_name=full_name,
            first_name=first_name,
            last_name=last_name,
        )
        session.add(profile)
        await session.commit()
    return membership


async def _seed_user(
    session: AsyncSession,
    *,
    org_id: str,
    roles: dict[str, object],
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    employee_id: str,
    is_superuser: bool,
    role_names: list[str],
) -> None:
    stmt = select(User).where(User.org_id == org_id, User.email == email)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            org_id=org_id,
            email=email,
            hashed_password=get_password_hash(password),
            is_active=True,
            is_superuser=is_superuser,
            token_version=0,
            must_change_password=False,
        )
        session.add(user)
        await session.commit()

    for role_name in role_names:
        role = roles.get(role_name)
        if role:
            await ensure_user_in_role(session, org_id, user.id, role)

    full_name = f"{first_name} {last_name}".strip()
    await _ensure_membership_and_profile(
        session,
        org_id=org_id,
        user=user,
        employee_id=employee_id,
        full_name=full_name,
        first_name=first_name,
        last_name=last_name,
    )


async def init_db() -> None:
    """
    Seed the database with initial orgs and users.
    """
    async with AsyncSessionLocal() as session:
        print("Seeding database...")
        try:
            await session.execute(select(Org).limit(1))
        except ProgrammingError as exc:
            # Database not migrated yet (tables missing)
            print(f"Skipping seed: database not migrated ({exc})")
            return

        org_ids = _parse_seed_org_ids()
        for org_id in org_ids:
            await _ensure_org(session, org_id)

        await ensure_audit_partitions_for_orgs(session)

        for org_id in org_ids:
            roles = await seed_system_roles(session, org_id)
            admin_is_superuser = org_id == settings.default_org_id
            await _seed_user(
                session,
                org_id=org_id,
                roles=roles,
                email=settings.seed_admin_email,
                password=settings.seed_admin_password,
                first_name=settings.seed_admin_full_name,
                last_name="",
                employee_id=f"{org_id}-admin",
                is_superuser=admin_is_superuser,
                role_names=["ORG_ADMIN", "EMPLOYEE"],
            )

            if _is_production():
                continue

            # Demo users (dev/staging only)
            demo_users = [
                {
                    "email": f"hr-{org_id}@example.com",
                    "first_name": "Harper",
                    "last_name": "HR",
                    "employee_id": f"{org_id}-HR-001",
                    "role_names": ["HR", "EMPLOYEE"],
                },
                {
                    "email": f"employee-{org_id}@example.com",
                    "first_name": "Evan",
                    "last_name": "Employee",
                    "employee_id": f"{org_id}-EMP-001",
                    "role_names": ["EMPLOYEE"],
                },
            ]
            for demo in demo_users:
                await _seed_user(
                    session,
                    org_id=org_id,
                    roles=roles,
                    email=demo["email"],
                    password=settings.seed_admin_password,
                    first_name=demo["first_name"],
                    last_name=demo["last_name"],
                    employee_id=demo["employee_id"],
                    is_superuser=False,
                    role_names=demo["role_names"],
                )


if __name__ == "__main__":
    asyncio.run(init_db())
