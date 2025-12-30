from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.vesting_event import VestingEvent


@dataclass(frozen=True)
class NextVestingEvent:
    vest_date: date
    shares: int


@dataclass(frozen=True)
class VestingTotals:
    total_granted_shares: int
    total_vested_shares: int
    total_unvested_shares: int
    next_vesting_event: NextVestingEvent | None


@dataclass(frozen=True)
class GrantVestingSummary:
    grant_id: UUID
    grant_date: date
    total_shares: int
    vested_shares: int
    unvested_shares: int
    exercise_price: Decimal


def _normalize_strategy(value: str | None) -> str:
    return (value or "SCHEDULED").upper()


def _grant_total_shares(grant: EmployeeStockGrant) -> int:
    return int(grant.total_shares or 0)


def compute_grant_vesting(grant: EmployeeStockGrant, as_of: date) -> tuple[int, int]:
    total = _grant_total_shares(grant)
    strategy = _normalize_strategy(grant.vesting_strategy)
    if strategy == "IMMEDIATE":
        vested = total if grant.grant_date <= as_of else 0
    else:
        vested = sum(int(event.shares) for event in grant.vesting_events if event.vest_date <= as_of)
    vested = min(vested, total)
    unvested = max(total - vested, 0)
    return int(vested), int(unvested)


def next_vesting_event(grants: Iterable[EmployeeStockGrant], as_of: date) -> NextVestingEvent | None:
    upcoming: dict[date, int] = {}
    for grant in grants:
        strategy = _normalize_strategy(grant.vesting_strategy)
        if strategy == "IMMEDIATE":
            if grant.grant_date > as_of:
                upcoming[grant.grant_date] = upcoming.get(grant.grant_date, 0) + _grant_total_shares(grant)
            continue
        for event in grant.vesting_events:
            if event.vest_date > as_of:
                upcoming[event.vest_date] = upcoming.get(event.vest_date, 0) + int(event.shares)

    if not upcoming:
        return None
    next_date = min(upcoming)
    return NextVestingEvent(vest_date=next_date, shares=int(upcoming[next_date]))


def build_grant_summaries(grants: Iterable[EmployeeStockGrant], as_of: date) -> list[GrantVestingSummary]:
    summaries: list[GrantVestingSummary] = []
    for grant in grants:
        vested, unvested = compute_grant_vesting(grant, as_of)
        summaries.append(
            GrantVestingSummary(
                grant_id=grant.id,
                grant_date=grant.grant_date,
                total_shares=_grant_total_shares(grant),
                vested_shares=vested,
                unvested_shares=unvested,
                exercise_price=Decimal(grant.exercise_price),
            )
        )
    return summaries


def aggregate_vesting(grants: Iterable[EmployeeStockGrant], as_of: date) -> VestingTotals:
    total_granted = 0
    total_vested = 0
    total_unvested = 0
    for grant in grants:
        total_granted += _grant_total_shares(grant)
        vested, unvested = compute_grant_vesting(grant, as_of)
        total_vested += vested
        total_unvested += unvested

    return VestingTotals(
        total_granted_shares=int(total_granted),
        total_vested_shares=int(total_vested),
        total_unvested_shares=int(total_unvested),
        next_vesting_event=next_vesting_event(grants, as_of),
    )


async def load_active_grants(
    db: AsyncSession, ctx: deps.TenantContext, membership_id: UUID
) -> list[EmployeeStockGrant]:
    stmt = (
        select(EmployeeStockGrant)
        .options(selectinload(EmployeeStockGrant.vesting_events))
        .where(
            EmployeeStockGrant.org_id == ctx.org_id,
            EmployeeStockGrant.org_membership_id == membership_id,
            EmployeeStockGrant.status == "ACTIVE",
        )
        .order_by(EmployeeStockGrant.grant_date.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()
