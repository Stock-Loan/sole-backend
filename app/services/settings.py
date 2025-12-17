from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.org_settings import OrgSettings
from app.schemas.settings import OrgSettingsBase, OrgSettingsUpdate


DEFAULT_SETTINGS = OrgSettingsBase()


async def get_org_settings(
    db: AsyncSession, ctx: deps.TenantContext, create_if_missing: bool = True
) -> OrgSettings:
    stmt = select(OrgSettings).where(OrgSettings.org_id == ctx.org_id)
    result = await db.execute(stmt)
    settings = result.scalar_one_or_none()
    if settings:
        return settings

    if not create_if_missing:
        raise ValueError("Org settings not found")

    settings = OrgSettings(
        org_id=ctx.org_id,
        allow_user_data_export=DEFAULT_SETTINGS.allow_user_data_export,
        allow_profile_edit=DEFAULT_SETTINGS.allow_profile_edit,
        require_two_factor=DEFAULT_SETTINGS.require_two_factor,
        audit_log_retention_days=DEFAULT_SETTINGS.audit_log_retention_days,
        inactive_user_retention_days=DEFAULT_SETTINGS.inactive_user_retention_days,
    )
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return settings


async def update_org_settings(
    db: AsyncSession, ctx: deps.TenantContext, payload: OrgSettingsUpdate
) -> OrgSettings:
    settings = await get_org_settings(db, ctx, create_if_missing=True)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(settings, field, value)
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return settings
