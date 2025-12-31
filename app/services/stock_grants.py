from __future__ import annotations

from datetime import date
from typing import Iterable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.audit_log import AuditLog
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.models.vesting_event import VestingEvent
from app.schemas.stock import (
    EmployeeStockGrantCreate,
    EmployeeStockGrantUpdate,
    NextVestingEvent,
    StockGrantPreviewResponse,
    VestingEventBase,
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
    if any(event.vest_date < grant_date for event in events):
        raise ValueError("Vesting events cannot occur before grant_date")
    total_event_shares = _sum_event_shares(events)
    if total_event_shares != total_shares:
        raise ValueError("Sum of vesting_events.shares must equal total_shares")
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
    if any(event.vest_date < grant_date for event in events):
        raise ValueError("Vesting events cannot occur before grant_date")
    total_event_shares = _sum_event_shares(events)
    if total_event_shares != total_shares:
        raise ValueError("Sum of vesting_events.shares must equal total_shares")


def _apply_vesting_summary(grant: EmployeeStockGrant, as_of: date | None = None) -> None:
    as_of_date = as_of or date.today()
    vested = sum(event.shares for event in grant.vesting_events if event.vest_date <= as_of_date)
    unvested = max(int(grant.total_shares) - int(vested), 0)
    grant.vested_shares = int(vested)
    grant.unvested_shares = int(unvested)
    grant.next_vesting_event = _next_vesting_event(grant, as_of_date)
    if grant.next_vesting_event:
        grant.next_vesting_summary = (
            f"Next vesting on {grant.next_vesting_event.vest_date.isoformat()} "
            f"({grant.next_vesting_event.shares} shares)"
        )
    else:
        grant.next_vesting_summary = None


def _next_vesting_event(
    grant: EmployeeStockGrant,
    as_of_date: date,
) -> NextVestingEvent | None:
    strategy = _normalize_strategy(grant.vesting_strategy)
    if strategy == VestingStrategy.IMMEDIATE.value:
        if grant.grant_date > as_of_date:
            return NextVestingEvent(
                vest_date=grant.grant_date,
                shares=int(grant.total_shares),
            )
        return None
    upcoming = [event for event in grant.vesting_events if event.vest_date > as_of_date]
    if not upcoming:
        return None
    next_event = min(upcoming, key=lambda event: event.vest_date)
    return NextVestingEvent(
        vest_date=next_event.vest_date,
        shares=int(next_event.shares),
    )


def _next_preview_event(
    strategy: VestingStrategy | str,
    grant_date: date,
    total_shares: int,
    events: list[VestingEventBase],
    as_of_date: date,
) -> NextVestingEvent | None:
    normalized_strategy = _normalize_strategy(strategy)
    if normalized_strategy == VestingStrategy.IMMEDIATE.value:
        if grant_date > as_of_date:
            return NextVestingEvent(vest_date=grant_date, shares=int(total_shares))
        return None
    upcoming = [event for event in events if event.vest_date > as_of_date]
    if not upcoming:
        return None
    next_event = min(upcoming, key=lambda event: event.vest_date)
    return NextVestingEvent(vest_date=next_event.vest_date, shares=int(next_event.shares))


def preview_grant(
    payload: EmployeeStockGrantCreate,
    as_of_date: date,
) -> StockGrantPreviewResponse:
    events = _build_vesting_events(
        payload.vesting_strategy,
        payload.grant_date,
        payload.total_shares,
        payload.vesting_events,
    )
    next_event = _next_preview_event(
        payload.vesting_strategy,
        payload.grant_date,
        payload.total_shares,
        events,
        as_of_date,
    )
    summary = (
        f"Next vesting on {next_event.vest_date.isoformat()} ({next_event.shares} shares)"
        if next_event
        else None
    )
    return StockGrantPreviewResponse(
        grant_date=payload.grant_date,
        total_shares=payload.total_shares,
        exercise_price=payload.exercise_price,
        vesting_strategy=payload.vesting_strategy,
        notes=payload.notes,
        vesting_events=events,
        next_vesting_event=next_event,
        next_vesting_summary=summary,
    )


def _grant_snapshot(grant: EmployeeStockGrant) -> dict:
    return {
        "id": str(grant.id),
        "org_id": grant.org_id,
        "org_membership_id": str(grant.org_membership_id),
        "grant_date": grant.grant_date.isoformat(),
        "total_shares": int(grant.total_shares),
        "exercise_price": str(grant.exercise_price),
        "status": grant.status,
        "vesting_strategy": grant.vesting_strategy,
        "notes": grant.notes,
        "vesting_events": [
            {"vest_date": event.vest_date.isoformat(), "shares": int(event.shares)}
            for event in grant.vesting_events
        ],
    }


async def _record_audit_log(
    db: AsyncSession,
    *,
    org_id: str,
    actor_id,
    action: str,
    grant: EmployeeStockGrant,
    old_value: dict | None,
) -> None:
    entry = AuditLog(
        org_id=org_id,
        actor_id=actor_id,
        action=action,
        resource_type="employee_stock_grant",
        resource_id=str(grant.id),
        old_value=old_value,
        new_value=_grant_snapshot(grant),
    )
    db.add(entry)
    await db.commit()


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
    *,
    actor_id=None,
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
    await _record_audit_log(
        db,
        org_id=ctx.org_id,
        actor_id=actor_id,
        action="stock_grant.created",
        grant=grant,
        old_value=None,
    )
    return grant


async def update_grant(
    db: AsyncSession,
    ctx: deps.TenantContext,
    grant: EmployeeStockGrant,
    payload: EmployeeStockGrantUpdate,
    *,
    actor_id=None,
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
    old_snapshot = _grant_snapshot(grant)

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
    await _record_audit_log(
        db,
        org_id=ctx.org_id,
        actor_id=actor_id,
        action="stock_grant.updated",
        grant=grant,
        old_value=old_snapshot,
    )
    return grant
