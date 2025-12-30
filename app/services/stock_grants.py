from __future__ import annotations

from datetime import date
from typing import Iterable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.models.vesting_event import VestingEvent
from app.schemas.stock import (
    EmployeeStockGrantCreate,
    EmployeeStockGrantUpdate,
    VestingEventCreate,
    VestingStrategy,
)


def _ensure_membership_active(membership: OrgMembership) -> None:
    employment = (membership.employment_status or "").upper()
    platform = (membership.platform_status or "").upper()
    if employment != "ACTIVE" or platform != "ACTIVE":
        raise ValueError("Membership must be ACTIVE to assign stock grants")


def _normalize_strategy(value: VestingStrategy | str) -> str:
    return value.value if isinstance(value, VestingStrategy) else str(value).upper()


def _sum_event_shares(events: Iterable[VestingEventCreate | VestingEvent]) -> int:
    return sum(int(e.shares) for e in events)


def _build_vesting_events(
    strategy: VestingStrategy | str,
    grant_date: date,
    total_shares: int,
    events: list[VestingEventCreate],
) -> list[VestingEventCreate]:
    normalized_strategy = _normalize_strategy(strategy)
    if normalized_strategy == VestingStrategy.IMMEDIATE.value:
        if events:
            if len(events) != 1:
                raise ValueError("Immediate vesting requires a single vesting event")
            event = events[0]
            if event.shares != total_shares:
                raise ValueError("Immediate vesting event must match total_shares")
            if event.vest_date != grant_date:
                raise ValueError("Immediate vesting event must use grant_date as vest_date")
            return events
        return [VestingEventCreate(vest_date=grant_date, shares=total_shares)]

    # Scheduled vesting requires explicit events
    if not events:
        raise ValueError("Scheduled vesting requires vesting_events")
    if _sum_event_shares(events) > total_shares:
        raise ValueError("Sum of vesting_events.shares cannot exceed total_shares")
    return events


def _validate_existing_events(
    events: list[VestingEvent],
    strategy: VestingStrategy | str,
    grant_date: date,
    total_shares: int,
) -> None:
    normalized_strategy = _normalize_strategy(strategy)
    if normalized_strategy == VestingStrategy.IMMEDIATE.value:
        if len(events) != 1:
            raise ValueError("Immediate vesting requires a single vesting event")
        event = events[0]
        if event.shares != total_shares or event.vest_date != grant_date:
            raise ValueError("Immediate vesting event must match total_shares and grant_date")
        return
    if not events:
        raise ValueError("Scheduled vesting requires vesting_events")
    if _sum_event_shares(events) > total_shares:
        raise ValueError("Sum of vesting_events.shares cannot exceed total_shares")


def _apply_vesting_summary(grant: EmployeeStockGrant, as_of: date | None = None) -> None:
    as_of_date = as_of or date.today()
    vested = sum(event.shares for event in grant.vesting_events if event.vest_date <= as_of_date)
    unvested = max(int(grant.total_shares) - int(vested), 0)
    grant.vested_shares = int(vested)
    grant.unvested_shares = int(unvested)


async def get_membership(
    db: AsyncSession, ctx: deps.TenantContext, membership_id: UUID
) -> OrgMembership | None:
    stmt = select(OrgMembership).where(
        OrgMembership.id == membership_id, OrgMembership.org_id == ctx.org_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_grants(
    db: AsyncSession, ctx: deps.TenantContext, membership_id: UUID
) -> list[EmployeeStockGrant]:
    stmt = (
        select(EmployeeStockGrant)
        .options(selectinload(EmployeeStockGrant.vesting_events))
        .where(
            EmployeeStockGrant.org_id == ctx.org_id,
            EmployeeStockGrant.org_membership_id == membership_id,
        )
        .order_by(EmployeeStockGrant.grant_date.desc())
    )
    result = await db.execute(stmt)
    grants = result.scalars().all()
    for grant in grants:
        _apply_vesting_summary(grant)
    return grants


async def get_grant(
    db: AsyncSession, ctx: deps.TenantContext, grant_id: UUID
) -> EmployeeStockGrant | None:
    stmt = (
        select(EmployeeStockGrant)
        .options(selectinload(EmployeeStockGrant.vesting_events))
        .where(EmployeeStockGrant.org_id == ctx.org_id, EmployeeStockGrant.id == grant_id)
    )
    result = await db.execute(stmt)
    grant = result.scalar_one_or_none()
    if grant:
        _apply_vesting_summary(grant)
    return grant


async def create_grant(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership_id: UUID,
    payload: EmployeeStockGrantCreate,
) -> EmployeeStockGrant:
    membership = await get_membership(db, ctx, membership_id)
    if not membership:
        raise ValueError("Membership not found")
    _ensure_membership_active(membership)

    events = _build_vesting_events(
        payload.vesting_strategy,
        payload.grant_date,
        payload.total_shares,
        payload.vesting_events,
    )

    grant = EmployeeStockGrant(
        org_id=ctx.org_id,
        org_membership_id=membership_id,
        grant_date=payload.grant_date,
        total_shares=payload.total_shares,
        exercise_price=payload.exercise_price,
        status="ACTIVE",
        vesting_strategy=_normalize_strategy(payload.vesting_strategy),
        notes=payload.notes,
    )
    if grant.id is None:
        grant.id = uuid4()

    grant.vesting_events = [
        VestingEvent(
            org_id=ctx.org_id,
            grant_id=grant.id,
            vest_date=event.vest_date,
            shares=event.shares,
        )
        for event in events
    ]

    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    _apply_vesting_summary(grant)
    return grant


async def update_grant(
    db: AsyncSession,
    ctx: deps.TenantContext,
    grant: EmployeeStockGrant,
    payload: EmployeeStockGrantUpdate,
) -> EmployeeStockGrant:
    membership = await get_membership(db, ctx, grant.org_membership_id)
    if not membership:
        raise ValueError("Membership not found")
    _ensure_membership_active(membership)

    data = payload.model_dump(exclude_unset=True)
    updated_total_shares = data.get("total_shares", grant.total_shares)
    updated_grant_date = data.get("grant_date", grant.grant_date)
    updated_strategy = data.get("vesting_strategy", grant.vesting_strategy)

    await db.refresh(grant, attribute_names=["vesting_events"])

    if payload.vesting_events is not None:
        events = _build_vesting_events(
            updated_strategy,
            updated_grant_date,
            updated_total_shares,
            payload.vesting_events,
        )
        grant.vesting_events = [
            VestingEvent(
                org_id=ctx.org_id,
                grant_id=grant.id,
                vest_date=event.vest_date,
                shares=event.shares,
            )
            for event in events
        ]
    else:
        _validate_existing_events(
            grant.vesting_events,
            updated_strategy,
            updated_grant_date,
            updated_total_shares,
        )

    for field, value in data.items():
        if field == "vesting_strategy" and value is not None:
            setattr(grant, field, _normalize_strategy(value))
        elif field != "vesting_events":
            setattr(grant, field, value)

    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    _apply_vesting_summary(grant)
    return grant
