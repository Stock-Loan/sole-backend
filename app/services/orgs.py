import re

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.settings import settings
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


async def _ensure_audit_partitions(db: AsyncSession, org_id: str) -> None:
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


async def create_org(
    db: AsyncSession,
    *,
    payload: OrgCreateRequest,
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

    await _ensure_audit_partitions(db, org.id)
    await authz.seed_system_roles(db, org.id)
    await settings_service.get_org_settings(db, deps.TenantContext(org_id=org.id), create_if_missing=True)
    return org
