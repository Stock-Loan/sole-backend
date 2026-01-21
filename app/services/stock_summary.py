from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.schemas.stock import (
    GrantSummary,
    NextVestingEvent,
    StockMembershipSnapshot,
    StockPolicySnapshot,
    StockReservationSummary,
    StockSummaryResponse,
)
from app.services import (
    eligibility,
    settings as settings_service,
    stock_reservations,
    vesting_engine,
)
from app.utils.redis_client import get_redis_client


CACHE_TTL_SECONDS = 300


def _cache_key(org_id: str, membership_id: UUID, as_of_date: date) -> str:
    return f"stock_summary:{org_id}:{membership_id}:{as_of_date.isoformat()}"


async def _get_cached_summary(
    org_id: str, membership_id: UUID, as_of_date: date
) -> StockSummaryResponse | None:
    try:
        redis = get_redis_client()
        cached = await redis.get(_cache_key(org_id, membership_id, as_of_date))
        if cached:
            return StockSummaryResponse.model_validate_json(cached)
    except Exception:
        return None
    return None


async def _set_cached_summary(
    summary: StockSummaryResponse, org_id: str, membership_id: UUID, as_of_date: date
) -> None:
    try:
        redis = get_redis_client()
        await redis.setex(
            _cache_key(org_id, membership_id, as_of_date),
            CACHE_TTL_SECONDS,
            summary.model_dump_json(),
        )
    except Exception:
        return None


async def invalidate_stock_summary_cache(org_id: str, membership_id: UUID) -> None:
    pattern = f"stock_summary:{org_id}:{membership_id}:*"
    try:
        redis = get_redis_client()
        async for key in redis.scan_iter(match=pattern, count=500):
            await redis.delete(key)
    except Exception:
        return None


async def invalidate_org_stock_summary_cache(org_id: str) -> None:
    pattern = f"stock_summary:{org_id}:*"
    try:
        redis = get_redis_client()
        async for key in redis.scan_iter(match=pattern, count=500):
            await redis.delete(key)
    except Exception:
        return None


async def get_membership(
    db: AsyncSession, ctx: deps.TenantContext, membership_id: UUID
) -> OrgMembership | None:
    stmt = select(OrgMembership).where(
        OrgMembership.id == membership_id, OrgMembership.org_id == ctx.org_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def build_stock_summary_from_data(
    *,
    membership: OrgMembership,
    org_settings: OrgSettings,
    grants: list,
    as_of_date: date,
    reserved_by_grant: dict[UUID, int] | None = None,
    reservations: list | None = None,
) -> StockSummaryResponse:
    reserved_by_grant = reserved_by_grant or {}
    reservations = reservations or []
    grants_by_id = {grant.id: grant for grant in grants}
    totals = vesting_engine.aggregate_vesting(grants, as_of_date)
    total_reserved = sum(reserved_by_grant.values())
    total_available_vested = max(totals.total_vested_shares - total_reserved, 0)
    eligibility_totals = vesting_engine.VestingTotals(
        total_granted_shares=totals.total_granted_shares,
        total_vested_shares=totals.total_vested_shares,
        total_unvested_shares=totals.total_unvested_shares,
        next_vesting_event=totals.next_vesting_event,
    )
    summaries = vesting_engine.build_grant_summaries(grants, as_of_date)
    eligibility_result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=org_settings,
        totals=eligibility_totals,
        as_of_date=as_of_date,
    )
    exercise_prices = [summary.exercise_price for summary in summaries]
    exercise_price_min = min(exercise_prices) if exercise_prices else None
    exercise_price_max = max(exercise_prices) if exercise_prices else None
    next_event = (
        NextVestingEvent(
            vest_date=totals.next_vesting_event.vest_date,
            shares=totals.next_vesting_event.shares,
        )
        if totals.next_vesting_event
        else None
    )
    upcoming_events = [
        NextVestingEvent(vest_date=event.vest_date, shares=event.shares)
        for event in vesting_engine.upcoming_vesting_events(grants, as_of_date, limit=3)
    ]
    return StockSummaryResponse(
        as_of_date=as_of_date,
        org_membership_id=membership.id,
        grant_count=len(grants),
        total_granted_shares=totals.total_granted_shares,
        total_vested_shares=totals.total_vested_shares,
        total_unvested_shares=totals.total_unvested_shares,
        total_reserved_shares=total_reserved,
        total_available_vested_shares=total_available_vested,
        next_vesting_event=next_event,
        next_vesting_events=upcoming_events,
        eligibility_result=eligibility_result,
        policy_snapshot=StockPolicySnapshot(
            min_vested_shares_to_exercise=org_settings.min_vested_shares_to_exercise,
            enforce_min_vested_to_exercise=org_settings.enforce_min_vested_to_exercise,
            min_service_duration_years=org_settings.min_service_duration_years,
            enforce_service_duration_rule=org_settings.enforce_service_duration_rule,
        ),
        membership_snapshot=StockMembershipSnapshot(
            employment_status=membership.employment_status,
            platform_status=membership.platform_status,
            employment_start_date=membership.employment_start_date,
        ),
        exercise_price_min=exercise_price_min,
        exercise_price_max=exercise_price_max,
        active_reservations=[
            StockReservationSummary(
                reservation_id=reservation.id,
                loan_application_id=reservation.loan_application_id,
                grant_id=reservation.grant_id,
                shares_reserved=int(reservation.shares_reserved),
                status=reservation.status,
                created_at=reservation.created_at,
            )
            for reservation in reservations
        ],
        grants=[
            GrantSummary(
                grant_id=summary.grant_id,
                grant_date=summary.grant_date,
                total_shares=summary.total_shares,
                vested_shares=summary.vested_shares,
                unvested_shares=summary.unvested_shares,
                reserved_shares=reserved_by_grant.get(summary.grant_id, 0),
                available_vested_shares=max(
                    summary.vested_shares - reserved_by_grant.get(summary.grant_id, 0), 0
                ),
                exercise_price=summary.exercise_price,
                vesting_strategy=getattr(
                    grants_by_id.get(summary.grant_id), "vesting_strategy", None
                ),
                status=getattr(grants_by_id.get(summary.grant_id), "status", None),
            )
            for summary in summaries
        ],
    )


async def build_stock_summary(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership_id: UUID,
    as_of_date: date,
) -> StockSummaryResponse:
    cached = await _get_cached_summary(ctx.org_id, membership_id, as_of_date)
    if cached:
        return cached
    membership = await get_membership(db, ctx, membership_id)
    if not membership:
        raise ValueError("Membership not found")
    org_settings = await settings_service.get_org_settings(db, ctx)
    grants = await vesting_engine.load_active_grants(db, ctx, membership_id)
    reserved_by_grant = {}
    reservations = []
    if grants:
        reserved_by_grant = await stock_reservations.get_active_reservations_by_grant(
            db, ctx, membership_id=membership_id, grant_ids=[grant.id for grant in grants]
        )
        reservations = await stock_reservations.list_active_reservations(
            db, ctx, membership_id=membership_id
        )
    summary = build_stock_summary_from_data(
        membership=membership,
        org_settings=org_settings,
        grants=grants,
        as_of_date=as_of_date,
        reserved_by_grant=reserved_by_grant,
        reservations=reservations,
    )
    await _set_cached_summary(summary, ctx.org_id, membership_id, as_of_date)
    return summary
