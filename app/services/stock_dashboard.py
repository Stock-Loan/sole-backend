from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.schemas.stock import (
    EligibilityReasonCode,
    StockDashboardSummary,
    StockDashboardTotals,
    StockDashboardEligibility,
    StockDashboardVestingTimeline,
    StockDashboardGrantMix,
    StockDashboardExercisePriceRange,
    StockDashboardReservationPressure,
    StockDashboardMembershipSnapshot,
    NextVestingEvent,
)
from app.services import eligibility, settings as settings_service, stock_reservations, vesting_engine
from app.utils.redis_client import get_redis_client


CACHE_TTL_SECONDS = 300
TWOPLACES = Decimal("0.01")


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


async def invalidate_stock_dashboard_cache(org_id: str) -> None:
    pattern = f"stock_dashboard:{org_id}:*"
    try:
        redis = get_redis_client()
        async for key in redis.scan_iter(match=pattern, count=500):
            await redis.delete(key)
    except Exception:
        return None


def _categorize_ineligible(reasons: list) -> str:
    codes = {reason.code for reason in reasons}
    if EligibilityReasonCode.INSUFFICIENT_SERVICE_DURATION in codes:
        return "service"
    if EligibilityReasonCode.BELOW_MIN_VESTED_THRESHOLD in codes:
        return "min_vested"
    return "other"


def _reserved_percent(total_reserved: int, total_vested: int) -> Decimal:
    if total_vested <= 0:
        return Decimal("0.00")
    return (Decimal(total_reserved) / Decimal(total_vested) * Decimal(100)).quantize(
        TWOPLACES, rounding=ROUND_HALF_UP
    )


def build_dashboard_summary_from_data(
    *,
    org_id: str,
    memberships: list[OrgMembership],
    org_settings,
    grants: list[EmployeeStockGrant],
    as_of_date: date,
    reserved_by_grant: dict | None = None,
    reserved_by_status: dict[str, int] | None = None,
    membership_platform_counts: dict[str, int] | None = None,
    membership_employment_counts: dict[str, int] | None = None,
    grant_status_counts: dict[str, int] | None = None,
    grant_strategy_counts: dict[str, int] | None = None,
) -> StockDashboardSummary:
    reserved_by_grant = reserved_by_grant or {}
    reserved_by_status = reserved_by_status or {}
    membership_platform_counts = membership_platform_counts or {}
    membership_employment_counts = membership_employment_counts or {}
    grant_status_counts = grant_status_counts or {}
    grant_strategy_counts = grant_strategy_counts or {}

    grant_count = len(grants)
    grants_by_membership: dict[UUID, list[EmployeeStockGrant]] = {}
    for grant in grants:
        grants_by_membership.setdefault(grant.org_membership_id, []).append(grant)

    totals = vesting_engine.aggregate_vesting(grants, as_of_date)
    next_event = vesting_engine.next_vesting_event(grants, as_of_date)
    upcoming_events = vesting_engine.upcoming_vesting_events(grants, as_of_date, limit=3)
    total_reserved = sum(reserved_by_grant.values())
    total_available_vested = max(totals.total_vested_shares - total_reserved, 0)
    exercise_prices = [grant.exercise_price for grant in grants]
    price_min = min(exercise_prices) if exercise_prices else None
    price_max = max(exercise_prices) if exercise_prices else None

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
        eligibility_totals = vesting_engine.VestingTotals(
            total_granted_shares=member_totals.total_granted_shares,
            total_vested_shares=member_totals.total_vested_shares,
            total_unvested_shares=member_totals.total_unvested_shares,
            next_vesting_event=member_totals.next_vesting_event,
        )
        result = eligibility.evaluate_eligibility_from_totals(
            membership=membership,
            org_settings=org_settings,
            totals=eligibility_totals,
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
        as_of=as_of_date,
        totals=StockDashboardTotals(
            program_employees=len(grants_by_membership),
            grant_count=grant_count,
            total_granted_shares=totals.total_granted_shares,
            total_vested_shares=totals.total_vested_shares,
            total_unvested_shares=totals.total_unvested_shares,
            total_reserved_shares=total_reserved,
            total_available_vested_shares=total_available_vested,
        ),
        eligibility=StockDashboardEligibility(
            eligible_to_exercise_count=eligible_count,
            not_eligible_due_to_service_count=service_count,
            not_eligible_due_to_min_vested_count=min_vested_count,
            not_eligible_due_to_other_count=other_count,
        ),
        vesting_timeline=StockDashboardVestingTimeline(
            next_vesting_date=next_event.vest_date if next_event else None,
            next_vesting_shares=next_event.shares if next_event else None,
            upcoming_events=[
                NextVestingEvent(vest_date=event.vest_date, shares=event.shares)
                for event in upcoming_events
            ],
        ),
        grant_mix=StockDashboardGrantMix(
            by_status=grant_status_counts,
            by_vesting_strategy=grant_strategy_counts,
        ),
        exercise_price_range=StockDashboardExercisePriceRange(min=price_min, max=price_max),
        reservation_pressure=StockDashboardReservationPressure(
            reserved_share_percent_of_vested=_reserved_percent(total_reserved, totals.total_vested_shares),
            reserved_by_status=reserved_by_status,
        ),
        membership_snapshot=StockDashboardMembershipSnapshot(
            by_platform_status=membership_platform_counts,
            by_employment_status=membership_employment_counts,
        ),
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
    reserved_by_grant = {}
    if grants:
        reserved_by_grant = await stock_reservations.get_active_reservations_by_grant_for_org(
            db, ctx, grant_ids=[grant.id for grant in grants]
        )
    reserved_by_status = await stock_reservations.sum_reserved_by_status_for_org(db, ctx)

    platform_counts_stmt = (
        select(OrgMembership.platform_status, func.count())
        .where(OrgMembership.org_id == ctx.org_id)
        .group_by(OrgMembership.platform_status)
    )
    platform_counts = {
        row[0]: row[1] for row in (await db.execute(platform_counts_stmt)).all() if row[0] is not None
    }
    employment_counts_stmt = (
        select(OrgMembership.employment_status, func.count())
        .where(OrgMembership.org_id == ctx.org_id)
        .group_by(OrgMembership.employment_status)
    )
    employment_counts = {
        row[0]: row[1] for row in (await db.execute(employment_counts_stmt)).all() if row[0] is not None
    }

    grant_status_stmt = (
        select(EmployeeStockGrant.status, func.count())
        .where(EmployeeStockGrant.org_id == ctx.org_id)
        .group_by(EmployeeStockGrant.status)
    )
    grant_status_counts = {
        row[0]: row[1] for row in (await db.execute(grant_status_stmt)).all() if row[0] is not None
    }
    grant_strategy_stmt = (
        select(EmployeeStockGrant.vesting_strategy, func.count())
        .where(EmployeeStockGrant.org_id == ctx.org_id)
        .group_by(EmployeeStockGrant.vesting_strategy)
    )
    grant_strategy_counts = {
        row[0]: row[1] for row in (await db.execute(grant_strategy_stmt)).all() if row[0] is not None
    }

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
        reserved_by_grant=reserved_by_grant,
        reserved_by_status=reserved_by_status,
        membership_platform_counts=platform_counts,
        membership_employment_counts=employment_counts,
        grant_status_counts=grant_status_counts,
        grant_strategy_counts=grant_strategy_counts,
    )
    await _set_cached_summary(summary, ctx.org_id, as_of_date)
    return summary
