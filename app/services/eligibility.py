from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.schemas.stock import EligibilityReason, EligibilityReasonCode, EligibilityResult
from app.services import settings as settings_service
from app.services import vesting_engine


def _membership_active(membership: OrgMembership) -> bool:
    return (membership.employment_status or "").upper() == "ACTIVE" and (
        membership.platform_status or ""
    ).upper() == "ACTIVE"


def evaluate_eligibility_from_totals(
    *,
    membership: OrgMembership,
    org_settings: OrgSettings,
    totals: vesting_engine.VestingTotals,
    as_of_date: date,
) -> EligibilityResult:
    reasons: list[EligibilityReason] = []

    if not _membership_active(membership):
        reasons.append(
            EligibilityReason(
                code=EligibilityReasonCode.EMPLOYMENT_INACTIVE,
                message="Employment or platform status is not active",
            )
        )

    if org_settings.enforce_service_duration_rule:
        if not membership.employment_start_date:
            reasons.append(
                EligibilityReason(
                    code=EligibilityReasonCode.INSUFFICIENT_SERVICE_DURATION,
                    message="Employment start date is missing",
                )
            )
        else:
            days = (as_of_date - membership.employment_start_date).days
            service_years = Decimal(days) / Decimal("365.25")
            required_value = org_settings.min_service_duration_years
            required = Decimal(str(required_value)) if required_value is not None else Decimal("0")
            if service_years < required:
                reasons.append(
                    EligibilityReason(
                        code=EligibilityReasonCode.INSUFFICIENT_SERVICE_DURATION,
                        message=f"Minimum service duration is {required} years",
                    )
                )

    if org_settings.enforce_min_vested_to_exercise:
        minimum = org_settings.min_vested_shares_to_exercise or 0
        if totals.total_vested_shares < minimum:
            reasons.append(
                EligibilityReason(
                    code=EligibilityReasonCode.BELOW_MIN_VESTED_THRESHOLD,
                    message=f"Minimum vested shares required is {minimum}",
                )
            )
    else:
        if totals.total_vested_shares <= 0:
            reasons.append(
                EligibilityReason(
                    code=EligibilityReasonCode.NO_VESTED_SHARES,
                    message="No vested shares are available to exercise",
                )
            )

    return EligibilityResult(
        eligible_to_exercise=len(reasons) == 0,
        total_granted_shares=totals.total_granted_shares,
        total_vested_shares=totals.total_vested_shares,
        total_unvested_shares=totals.total_unvested_shares,
        reasons=reasons,
    )


async def evaluate_exercise_eligibility(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership: OrgMembership,
    as_of_date: date,
) -> EligibilityResult:
    org_settings = await settings_service.get_org_settings(db, ctx)
    grants = await vesting_engine.load_active_grants(db, ctx, membership.id)
    totals = vesting_engine.aggregate_vesting(grants, as_of_date)
    return evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=org_settings,
        totals=totals,
        as_of_date=as_of_date,
    )
