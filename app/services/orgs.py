from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.settings import settings
from app.models.org import Org
from app.schemas.orgs import OrgCreateRequest
from app.services import authz, settings as settings_service


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

    await authz.seed_system_roles(db, org.id)
    await settings_service.get_org_settings(db, deps.TenantContext(org_id=org.id), create_if_missing=True)
    return org
