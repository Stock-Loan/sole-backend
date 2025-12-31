from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.audit_log import AuditLog
from app.models.org_settings import OrgSettings
from app.schemas.settings import (
    LoanInterestType,
    LoanRepaymentMethod,
    OrgSettingsBase,
    OrgSettingsUpdate,
)


DEFAULT_SETTINGS = OrgSettingsBase()
ALLOWED_REPAYMENT_METHODS = {method.value for method in LoanRepaymentMethod}
ALLOWED_INTEREST_TYPES = {interest.value for interest in LoanInterestType}


def _settings_snapshot(settings: OrgSettings) -> dict:
    data: dict[str, object] = {}
    for column in settings.__table__.columns:
        name = column.name
        if name in {"created_at", "updated_at"}:
            continue
        value = getattr(settings, name)
        if isinstance(value, datetime):
            value = value.isoformat()
        data[name] = value
    return data


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


def _normalize_enum_list(values: list) -> list[str]:
    normalized: list[str] = []
    for item in values:
        if isinstance(item, (LoanRepaymentMethod, LoanInterestType)):
            normalized.append(item.value)
        else:
            normalized.append(str(item))
    return normalized


def _validate_loan_policy(
    *,
    allowed_repayment_methods: list[str] | None,
    min_loan_term_months: int | None,
    max_loan_term_months: int | None,
    allowed_interest_types: list[str] | None,
    fixed_interest_rate_annual_percent,
    variable_base_rate_annual_percent,
    variable_margin_annual_percent,
    require_down_payment: bool,
    down_payment_percent,
) -> None:
    errors: list[str] = []

    if not allowed_repayment_methods:
        errors.append("allowed_repayment_methods must include at least one repayment method")
    else:
        unknown = [value for value in allowed_repayment_methods if value not in ALLOWED_REPAYMENT_METHODS]
        if unknown:
            errors.append(f"allowed_repayment_methods contains invalid values: {', '.join(unknown)}")

    if not allowed_interest_types:
        errors.append("allowed_interest_types must include at least one interest type")
    else:
        unknown = [value for value in allowed_interest_types if value not in ALLOWED_INTEREST_TYPES]
        if unknown:
            errors.append(f"allowed_interest_types contains invalid values: {', '.join(unknown)}")

    if min_loan_term_months is None or max_loan_term_months is None:
        errors.append("min_loan_term_months and max_loan_term_months are required")
    elif min_loan_term_months > max_loan_term_months:
        errors.append("min_loan_term_months must be <= max_loan_term_months")

    def _check_percent(value, field: str) -> None:
        if value is None:
            return
        if value < 0:
            errors.append(f"{field} must be >= 0")
        elif value > 100:
            errors.append(f"{field} must be <= 100")

    if fixed_interest_rate_annual_percent is None:
        errors.append("fixed_interest_rate_annual_percent is required")
    _check_percent(fixed_interest_rate_annual_percent, "fixed_interest_rate_annual_percent")
    _check_percent(variable_base_rate_annual_percent, "variable_base_rate_annual_percent")
    _check_percent(variable_margin_annual_percent, "variable_margin_annual_percent")
    _check_percent(down_payment_percent, "down_payment_percent")

    if require_down_payment:
        if down_payment_percent is None:
            errors.append("down_payment_percent is required when require_down_payment is true")
        elif down_payment_percent <= 0:
            errors.append("down_payment_percent must be > 0 when require_down_payment is true")
    else:
        if down_payment_percent is not None:
            errors.append("down_payment_percent must be null when require_down_payment is false")

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
        allowed_repayment_methods=_normalize_enum_list(
            list(DEFAULT_SETTINGS.allowed_repayment_methods)
        ),
        min_loan_term_months=DEFAULT_SETTINGS.min_loan_term_months,
        max_loan_term_months=DEFAULT_SETTINGS.max_loan_term_months,
        allowed_interest_types=_normalize_enum_list(
            list(DEFAULT_SETTINGS.allowed_interest_types)
        ),
        fixed_interest_rate_annual_percent=DEFAULT_SETTINGS.fixed_interest_rate_annual_percent,
        variable_base_rate_annual_percent=DEFAULT_SETTINGS.variable_base_rate_annual_percent,
        variable_margin_annual_percent=DEFAULT_SETTINGS.variable_margin_annual_percent,
        require_down_payment=DEFAULT_SETTINGS.require_down_payment,
        down_payment_percent=DEFAULT_SETTINGS.down_payment_percent,
    )
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return settings


async def update_org_settings(
    db: AsyncSession,
    ctx: deps.TenantContext,
    payload: OrgSettingsUpdate,
    *,
    actor_id=None,
) -> OrgSettings:
    settings = await get_org_settings(db, ctx, create_if_missing=True)
    old_snapshot = _settings_snapshot(settings)
    data = payload.model_dump(exclude_unset=True)
    if "allowed_repayment_methods" in data and data["allowed_repayment_methods"] is not None:
        data["allowed_repayment_methods"] = _normalize_enum_list(data["allowed_repayment_methods"])
    if "allowed_interest_types" in data and data["allowed_interest_types"] is not None:
        data["allowed_interest_types"] = _normalize_enum_list(data["allowed_interest_types"])
    if data.get("enforce_service_duration_rule") is False and "min_service_duration_days" not in data:
        data["min_service_duration_days"] = None
    if data.get("enforce_min_vested_to_exercise") is False and "min_vested_shares_to_exercise" not in data:
        data["min_vested_shares_to_exercise"] = None
    if data.get("require_down_payment") is False and "down_payment_percent" not in data:
        data["down_payment_percent"] = None
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
    loan_candidate = {
        "allowed_repayment_methods": data.get(
            "allowed_repayment_methods", settings.allowed_repayment_methods
        ),
        "min_loan_term_months": data.get("min_loan_term_months", settings.min_loan_term_months),
        "max_loan_term_months": data.get("max_loan_term_months", settings.max_loan_term_months),
        "allowed_interest_types": data.get(
            "allowed_interest_types", settings.allowed_interest_types
        ),
        "fixed_interest_rate_annual_percent": data.get(
            "fixed_interest_rate_annual_percent", settings.fixed_interest_rate_annual_percent
        ),
        "variable_base_rate_annual_percent": data.get(
            "variable_base_rate_annual_percent", settings.variable_base_rate_annual_percent
        ),
        "variable_margin_annual_percent": data.get(
            "variable_margin_annual_percent", settings.variable_margin_annual_percent
        ),
        "require_down_payment": data.get("require_down_payment", settings.require_down_payment),
        "down_payment_percent": data.get("down_payment_percent", settings.down_payment_percent),
    }
    if loan_candidate["allowed_repayment_methods"] is not None:
        loan_candidate["allowed_repayment_methods"] = _normalize_enum_list(
            list(loan_candidate["allowed_repayment_methods"])
        )
    if loan_candidate["allowed_interest_types"] is not None:
        loan_candidate["allowed_interest_types"] = _normalize_enum_list(
            list(loan_candidate["allowed_interest_types"])
        )
    _validate_loan_policy(**loan_candidate)
    for field, value in data.items():
        setattr(settings, field, value)
    new_snapshot = _settings_snapshot(settings)
    audit = AuditLog(
        org_id=ctx.org_id,
        actor_id=actor_id,
        action="org_settings.updated",
        resource_type="org_settings",
        resource_id=ctx.org_id,
        old_value=old_snapshot,
        new_value=new_snapshot,
    )
    db.add(audit)
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return settings
