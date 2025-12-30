from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.org_settings import OrgSettings
from app.schemas.settings import OrgSettingsBase, OrgSettingsUpdate


DEFAULT_SETTINGS = OrgSettingsBase()


def _validate_stock_rules(
    *,
    enforce_service_duration_rule: bool,
    min_service_duration_days: int | None,
    enforce_min_vested_to_exercise: bool,
    min_vested_shares_to_exercise: int | None,
) -> None:
    errors: list[str] = []

    if enforce_service_duration_rule:
        if min_service_duration_days is None:
            errors.append(
                "min_service_duration_days is required when enforce_service_duration_rule is true"
            )
        elif min_service_duration_days < 0:
            errors.append("min_service_duration_days must be >= 0")
    else:
        if min_service_duration_days is not None:
            errors.append(
                "min_service_duration_days must be null when enforce_service_duration_rule is false"
            )

    if enforce_min_vested_to_exercise:
        if min_vested_shares_to_exercise is None:
            errors.append(
                "min_vested_shares_to_exercise is required when enforce_min_vested_to_exercise is true"
            )
        elif min_vested_shares_to_exercise < 0:
            errors.append("min_vested_shares_to_exercise must be >= 0")
    else:
        if min_vested_shares_to_exercise is not None:
            errors.append(
                "min_vested_shares_to_exercise must be null when enforce_min_vested_to_exercise is false"
            )

    if errors:
        raise ValueError("; ".join(errors))


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
        enforce_service_duration_rule=DEFAULT_SETTINGS.enforce_service_duration_rule,
        min_service_duration_days=DEFAULT_SETTINGS.min_service_duration_days,
        enforce_min_vested_to_exercise=DEFAULT_SETTINGS.enforce_min_vested_to_exercise,
        min_vested_shares_to_exercise=DEFAULT_SETTINGS.min_vested_shares_to_exercise,
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
    if data.get("enforce_service_duration_rule") is False and "min_service_duration_days" not in data:
        data["min_service_duration_days"] = None
    if data.get("enforce_min_vested_to_exercise") is False and "min_vested_shares_to_exercise" not in data:
        data["min_vested_shares_to_exercise"] = None
    candidate = {
        "enforce_service_duration_rule": data.get(
            "enforce_service_duration_rule", settings.enforce_service_duration_rule
        ),
        "min_service_duration_days": data.get(
            "min_service_duration_days", settings.min_service_duration_days
        ),
        "enforce_min_vested_to_exercise": data.get(
            "enforce_min_vested_to_exercise", settings.enforce_min_vested_to_exercise
        ),
        "min_vested_shares_to_exercise": data.get(
            "min_vested_shares_to_exercise", settings.min_vested_shares_to_exercise
        ),
    }
    _validate_stock_rules(**candidate)
    for field, value in data.items():
        setattr(settings, field, value)
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return settings
