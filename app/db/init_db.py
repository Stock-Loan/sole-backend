import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from app.core.security import get_password_hash
from app.core.settings import settings
from app.db.session import AsyncSessionLocal
from app.models.org import Org
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.services.authz import (
    ensure_org_admin_for_seed_user,
    ensure_user_in_role,
    seed_system_roles,
)
from app.services.orgs import ensure_audit_partitions_for_orgs


async def init_db() -> None:
    """
    Seed the database with an initial user.
    """
    async with AsyncSessionLocal() as session:
        print("Seeding database...")
        # Ensure default org exists
        try:
            org_stmt = select(Org).where(Org.id == settings.default_org_id)
            org_result = await session.execute(org_stmt)
            org = org_result.scalar_one_or_none()
        except ProgrammingError as exc:
            # Database not migrated yet (tables missing)
            print(f"Skipping seed: database not migrated ({exc})")
            return
        if not org:
            print("Creating default org...")
            org = Org(
                id=settings.default_org_id,
                name=settings.default_org_name,
                slug=settings.default_org_slug or settings.default_org_id,
                status="ACTIVE",
            )
            session.add(org)
            await session.commit()

        await ensure_audit_partitions_for_orgs(session)

        # Ensure system roles exist for the default org
        roles = await seed_system_roles(session, settings.default_org_id)

        stmt = select(User).where(
            User.org_id == settings.default_org_id, User.email == settings.seed_admin_email
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            print("Creating admin user...")
            user = User(
                org_id=settings.default_org_id,
                email=settings.seed_admin_email,
                hashed_password=get_password_hash(settings.seed_admin_password),
                is_active=True,
                is_superuser=True,
                token_version=0,
                full_name=settings.seed_admin_full_name,
                must_change_password=False,
                first_name=settings.seed_admin_full_name,
            )
            session.add(user)
            await session.commit()
            print("Admin user created.")
        else:
            print("Admin user already exists.")

        # Ensure admin is assigned to ORG_ADMIN system role
        admin_role = roles.get("ORG_ADMIN")
        if admin_role and user:
            await ensure_user_in_role(session, settings.default_org_id, user.id, admin_role)
            # Also ensure admin has ORG_ADMIN in any additional orgs provided via settings (comma-separated)
            if settings.extra_seed_org_ids:
                org_ids = [settings.default_org_id] + [
                    oid.strip() for oid in settings.extra_seed_org_ids.split(",") if oid.strip()
                ]
                await ensure_org_admin_for_seed_user(session, user.id, org_ids)

        # Ensure admin also has EMPLOYEE role (everyone gets EMPLOYEE by default)
        employee_role = roles.get("EMPLOYEE")
        if employee_role and user:
            await ensure_user_in_role(session, settings.default_org_id, user.id, employee_role)

        # Ensure admin has an org membership in the default org for permission checks
        mem_stmt = select(OrgMembership).where(
            OrgMembership.org_id == settings.default_org_id, OrgMembership.user_id == user.id
        )
        mem_result = await session.execute(mem_stmt)
        membership = mem_result.scalar_one_or_none()
        if not membership:
            now = datetime.now(timezone.utc)
            membership = OrgMembership(
                org_id=settings.default_org_id,
                user_id=user.id,
                employee_id="admin",
                employment_status="ACTIVE",
                platform_status="ACTIVE",
                invitation_status="ACCEPTED",
                invited_at=now,
                accepted_at=now,
            )
            session.add(membership)
            await session.commit()
            print("Seed admin membership created for default org.")


if __name__ == "__main__":
    asyncio.run(init_db())
