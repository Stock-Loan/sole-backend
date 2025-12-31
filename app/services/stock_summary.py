from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.schemas.stock import GrantSummary, NextVestingEvent, StockSummaryResponse
from app.services import eligibility, settings as settings_service, vesting_engine
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
) -> StockSummaryResponse:
    totals = vesting_engine.aggregate_vesting(grants, as_of_date)
    summaries = vesting_engine.build_grant_summaries(grants, as_of_date)
    eligibility_result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=org_settings,
        totals=totals,
        as_of_date=as_of_date,
    )
    next_event = (
        NextVestingEvent(
            vest_date=totals.next_vesting_event.vest_date,
            shares=totals.next_vesting_event.shares,
        )
        if totals.next_vesting_event
        else None
    )
    return StockSummaryResponse(
        org_membership_id=membership.id,
        total_granted_shares=totals.total_granted_shares,
        total_vested_shares=totals.total_vested_shares,
        total_unvested_shares=totals.total_unvested_shares,
        next_vesting_event=next_event,
        eligibility_result=eligibility_result,
        grants=[
            GrantSummary(
                grant_id=summary.grant_id,
                grant_date=summary.grant_date,
                total_shares=summary.total_shares,
                vested_shares=summary.vested_shares,
                unvested_shares=summary.unvested_shares,
                exercise_price=summary.exercise_price,
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
    summary = build_stock_summary_from_data(
        membership=membership,
        org_settings=org_settings,
        grants=grants,
        as_of_date=as_of_date,
    )
    await _set_cached_summary(summary, ctx.org_id, membership_id, as_of_date)
    return summary
