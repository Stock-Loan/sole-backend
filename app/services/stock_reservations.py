from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.stock_grant_reservation import StockGrantReservation


ACTIVE_RESERVATION_STATUSES = {"SUBMITTED", "IN_REVIEW", "ACTIVE"}


async def get_active_reservations_by_grant(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    membership_id: UUID,
    grant_ids: list[UUID] | None = None,
) -> dict[UUID, int]:
    conditions = [
        StockGrantReservation.org_id == ctx.org_id,
        StockGrantReservation.org_membership_id == membership_id,
        StockGrantReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
    ]
    if grant_ids:
        conditions.append(StockGrantReservation.grant_id.in_(grant_ids))
    stmt = (
        select(
            StockGrantReservation.grant_id,
            func.coalesce(func.sum(StockGrantReservation.shares_reserved), 0),
        )
        .where(*conditions)
        .group_by(StockGrantReservation.grant_id)
    )
    result = await db.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def list_active_reservations(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    membership_id: UUID,
) -> list[StockGrantReservation]:
    stmt = (
        select(StockGrantReservation)
        .where(
            StockGrantReservation.org_id == ctx.org_id,
            StockGrantReservation.org_membership_id == membership_id,
            StockGrantReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        )
        .order_by(StockGrantReservation.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_active_reservations_by_grant_for_org(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    grant_ids: list[UUID] | None = None,
) -> dict[UUID, int]:
    conditions = [
        StockGrantReservation.org_id == ctx.org_id,
        StockGrantReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
    ]
    if grant_ids:
        conditions.append(StockGrantReservation.grant_id.in_(grant_ids))
    stmt = (
        select(
            StockGrantReservation.grant_id,
            func.coalesce(func.sum(StockGrantReservation.shares_reserved), 0),
        )
        .where(*conditions)
        .group_by(StockGrantReservation.grant_id)
    )
    result = await db.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def set_reservation_status_for_application(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    application_id: UUID,
    status: str,
) -> None:
    stmt = (
        select(StockGrantReservation)
        .where(
            StockGrantReservation.org_id == ctx.org_id,
            StockGrantReservation.loan_application_id == application_id,
        )
    )
    result = await db.execute(stmt)
    reservations = result.scalars().all()
    for reservation in reservations:
        reservation.status = status
        db.add(reservation)
