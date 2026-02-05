import re

from datetime import datetime, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.settings import settings
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.models.org import Org
from app.schemas.orgs import OrgCreateRequest
from app.services import authz, settings as settings_service


def _partition_suffix(org_id: str) -> str:
    # Normalize org_id to a safe identifier suffix (letters, numbers, underscore).
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", org_id).strip("_").lower()
    if not safe:
        safe = "org"
    if safe[0].isdigit():
        safe = f"org_{safe}"
    return safe


async def ensure_audit_partitions(db: AsyncSession, org_id: str) -> None:
    suffix = _partition_suffix(org_id)
    audit_table = f"audit_logs_{suffix}"
    journal_table = f"journal_entries_{suffix}"

    org_literal = org_id.replace("'", "''")
    await db.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {audit_table} "
            f"PARTITION OF audit_logs FOR VALUES IN ('{org_literal}')"
        )
    )
    await db.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {journal_table} "
            f"PARTITION OF journal_entries FOR VALUES IN ('{org_literal}')"
        )
    )
    await db.commit()


async def ensure_audit_partitions_for_orgs(db: AsyncSession) -> None:
    stmt = select(Org.id)
    rows = (await db.execute(stmt)).all()
    for (org_id,) in rows:
        await ensure_audit_partitions(db, org_id)


async def create_org(
    db: AsyncSession,
    *,
    payload: OrgCreateRequest,
    creator: User | None = None,
) -> Org:
    if settings.tenancy_mode != "multi":
        raise ValueError("Org creation is disabled in single-tenant mode")

    existing_stmt = select(Org).where(or_(Org.id == payload.org_id, Org.slug == payload.slug))
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing:
        raise ValueError("Org with same id or slug already exists")

    org = Org(
        id=payload.org_id,
        name=payload.name,
        slug=payload.slug,
        status="ACTIVE",
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)

    await ensure_audit_partitions(db, org.id)
    roles = await authz.seed_system_roles(db, org.id)
    await settings_service.get_org_settings(
        db, deps.TenantContext(org_id=org.id), create_if_missing=True
    )
    if creator:
        await _bootstrap_creator(db, org_id=org.id, creator=creator, roles=roles)
    return org


async def _bootstrap_creator(
    db: AsyncSession,
    *,
    org_id: str,
    creator: User,
    roles,
) -> None:
    user = creator

    admin_role = roles.get("ORG_ADMIN")
    if admin_role:
        await authz.ensure_user_in_role(db, org_id, user.id, admin_role)
    employee_role = roles.get("EMPLOYEE")
    if employee_role:
        await authz.ensure_user_in_role(db, org_id, user.id, employee_role)

    # Ensure org membership for permissions and directory lists.
    mem_stmt = select(OrgMembership).where(
        OrgMembership.org_id == org_id, OrgMembership.user_id == user.id
    )
    membership = (await db.execute(mem_stmt)).scalar_one_or_none()
    if not membership:
        now = datetime.now(timezone.utc)
        membership = OrgMembership(
            org_id=org_id,
            user_id=user.id,
            employee_id=f"admin-{user.id}",
            employment_status="ACTIVE",
            platform_status="ACTIVE",
            invitation_status="ACCEPTED",
            invited_at=now,
            accepted_at=now,
        )
        db.add(membership)
        await db.commit()
