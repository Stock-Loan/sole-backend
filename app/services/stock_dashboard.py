from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.schemas.stock import EligibilityReasonCode, StockDashboardSummary
from app.services import eligibility, settings as settings_service, vesting_engine
from app.utils.redis_client import get_redis_client


CACHE_TTL_SECONDS = 300


def _cache_key(org_id: str, as_of_date: date) -> str:
    return f"stock_dashboard:{org_id}:{as_of_date.isoformat()}"


async def _get_cached_summary(org_id: str, as_of_date: date) -> StockDashboardSummary | None:
    try:
        redis = get_redis_client()
        cached = await redis.get(_cache_key(org_id, as_of_date))
        if cached:
            return StockDashboardSummary.model_validate_json(cached)
    except Exception:
        return None
    return None


async def _set_cached_summary(summary: StockDashboardSummary, org_id: str, as_of_date: date) -> None:
    try:
        redis = get_redis_client()
        await redis.setex(
            _cache_key(org_id, as_of_date),
            CACHE_TTL_SECONDS,
            summary.model_dump_json(),
        )
    except Exception:
        return None


def _categorize_ineligible(reasons: list) -> str:
    codes = {reason.code for reason in reasons}
    if EligibilityReasonCode.INSUFFICIENT_SERVICE_DURATION in codes:
        return "service"
    if EligibilityReasonCode.BELOW_MIN_VESTED_THRESHOLD in codes:
        return "min_vested"
    return "other"


def build_dashboard_summary_from_data(
    *,
    org_id: str,
    memberships: list[OrgMembership],
    org_settings,
    grants: list[EmployeeStockGrant],
    as_of_date: date,
) -> StockDashboardSummary:
    grants_by_membership: dict[UUID, list[EmployeeStockGrant]] = {}
    for grant in grants:
        grants_by_membership.setdefault(grant.org_membership_id, []).append(grant)

    totals = vesting_engine.aggregate_vesting(grants, as_of_date)
    next_event = vesting_engine.next_vesting_event(grants, as_of_date)

    eligible_count = 0
    service_count = 0
    min_vested_count = 0
    other_count = 0

    memberships_by_id = {member.id: member for member in memberships}
    for membership_id, member_grants in grants_by_membership.items():
        membership = memberships_by_id.get(membership_id)
        if not membership:
            continue
        member_totals = vesting_engine.aggregate_vesting(member_grants, as_of_date)
        result = eligibility.evaluate_eligibility_from_totals(
            membership=membership,
            org_settings=org_settings,
            totals=member_totals,
            as_of_date=as_of_date,
        )
        if result.eligible_to_exercise:
            eligible_count += 1
        else:
            category = _categorize_ineligible(result.reasons)
            if category == "service":
                service_count += 1
            elif category == "min_vested":
                min_vested_count += 1
            else:
                other_count += 1

    return StockDashboardSummary(
        org_id=org_id,
        total_program_employees=len(grants_by_membership),
        total_granted_shares=totals.total_granted_shares,
        total_vested_shares=totals.total_vested_shares,
        total_unvested_shares=totals.total_unvested_shares,
        eligible_to_exercise_count=eligible_count,
        not_eligible_due_to_service_count=service_count,
        not_eligible_due_to_min_vested_count=min_vested_count,
        not_eligible_due_to_other_count=other_count,
        next_global_vesting_date=next_event.vest_date if next_event else None,
    )


async def build_dashboard_summary(
    db: AsyncSession, ctx: deps.TenantContext, as_of_date: date
) -> StockDashboardSummary:
    cached = await _get_cached_summary(ctx.org_id, as_of_date)
    if cached:
        return cached
    grants_stmt = (
        select(EmployeeStockGrant)
        .options(selectinload(EmployeeStockGrant.vesting_events))
        .where(
            EmployeeStockGrant.org_id == ctx.org_id,
            EmployeeStockGrant.status == "ACTIVE",
        )
        .order_by(EmployeeStockGrant.grant_date.desc())
    )
    grants = (await db.execute(grants_stmt)).scalars().all()
    membership_ids = {grant.org_membership_id for grant in grants}
    memberships: list[OrgMembership] = []
    if membership_ids:
        member_stmt = select(OrgMembership).where(
            OrgMembership.org_id == ctx.org_id, OrgMembership.id.in_(membership_ids)
        )
        memberships = (await db.execute(member_stmt)).scalars().all()
    org_settings = await settings_service.get_org_settings(db, ctx)
    summary = build_dashboard_summary_from_data(
        org_id=ctx.org_id,
        memberships=memberships,
        org_settings=org_settings,
        grants=grants,
        as_of_date=as_of_date,
    )
    await _set_cached_summary(summary, ctx.org_id, as_of_date)
    return summary
