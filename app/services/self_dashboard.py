from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.announcement import Announcement, AnnouncementRead
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.loan_application import LoanApplication
from app.models.loan_repayment import LoanRepayment
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import LoanApplicationStatus
from app.schemas.self_dashboard import (
    PendingAction,
    RepaymentHistoryItem,
    SelfDashboardAttention,
    SelfDashboardSummary,
    SelfGrantMix,
    SelfGrantSummary,
    SelfLoanRepaymentActivity,
    SelfLoanSummary,
    SelfStockReservations,
    SelfStockTotals,
    SelfVestingTimeline,
    VestedByMonth,
)
from app.schemas.stock import (
    StockGrantStatus,
    StockPolicySnapshot,
    StockReservationSummary,
    VestingStrategy,
)
from app.services import eligibility, loan_applications, settings as settings_service, stock_reservations, vesting_engine


TWOPLACES = Decimal("0.01")


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _month_index(as_of: date, value: date) -> int:
    return (value.year - as_of.year) * 12 + (value.month - as_of.month)


def _grant_next_vesting(grant: EmployeeStockGrant, as_of_date: date) -> tuple[date | None, int | None]:
    strategy = (grant.vesting_strategy or "SCHEDULED").upper()
    if strategy == VestingStrategy.IMMEDIATE.value:
        if grant.grant_date > as_of_date:
            return grant.grant_date, int(grant.total_shares)
        return None, None
    upcoming = [event for event in grant.vesting_events if event.vest_date > as_of_date]
    if not upcoming:
        return None, None
    next_event = min(upcoming, key=lambda event: event.vest_date)
    return next_event.vest_date, int(next_event.shares)


def _build_vested_by_month(
    grants: list[EmployeeStockGrant], as_of_date: date, months: int = 6
) -> list[VestedByMonth]:
    buckets: dict[str, int] = {}
    for grant in grants:
        strategy = (grant.vesting_strategy or "SCHEDULED").upper()
        if strategy == VestingStrategy.IMMEDIATE.value:
            if grant.grant_date >= as_of_date:
                index = _month_index(as_of_date, grant.grant_date)
                if 0 <= index < months:
                    key = _month_key(grant.grant_date)
                    buckets[key] = buckets.get(key, 0) + int(grant.total_shares)
            continue
        for event in grant.vesting_events:
            if event.vest_date >= as_of_date:
                index = _month_index(as_of_date, event.vest_date)
                if 0 <= index < months:
                    key = _month_key(event.vest_date)
                    buckets[key] = buckets.get(key, 0) + int(event.shares)
    return [VestedByMonth(month=month, shares=shares) for month, shares in sorted(buckets.items())]


async def _unread_announcements_count(db: AsyncSession, ctx: deps.TenantContext, user_id) -> int:
    unread_filter = ~exists().where(
        AnnouncementRead.announcement_id == Announcement.id,
        AnnouncementRead.user_id == user_id,
        AnnouncementRead.org_id == ctx.org_id,
    )
    count_stmt = select(func.count()).where(
        Announcement.org_id == ctx.org_id,
        unread_filter,
    )
    return int((await db.execute(count_stmt)).scalar_one() or 0)


async def _pending_actions(
    db: AsyncSession, ctx: deps.TenantContext, user_id, limit: int = 5
) -> tuple[int, list[PendingAction]]:
    base_stmt = (
        select(LoanWorkflowStage, LoanApplication.id)
        .join(LoanApplication, LoanApplication.id == LoanWorkflowStage.loan_application_id)
        .where(
            LoanWorkflowStage.org_id == ctx.org_id,
            LoanWorkflowStage.assigned_to_user_id == user_id,
            LoanWorkflowStage.status != "COMPLETED",
        )
        .order_by(LoanWorkflowStage.created_at.desc())
    )
    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = int((await db.execute(total_stmt)).scalar_one() or 0)
    rows = (await db.execute(base_stmt.limit(limit))).all()
    actions = [
        PendingAction(
            action_type="loan_workflow",
            label=f"Review loan {loan_id} ({stage.stage_type})",
            due_date=None,
            related_id=loan_id,
        )
        for stage, loan_id in rows
    ]
    return total, actions


async def build_self_dashboard_summary(
    db: AsyncSession,
    ctx: deps.TenantContext,
    user_id,
    as_of_date: date,
) -> SelfDashboardSummary:
    membership = await loan_applications.get_membership_for_user(db, ctx, user_id)
    if not membership:
        raise ValueError("Membership not found")

    org_settings = await settings_service.get_org_settings(db, ctx)
    grants = await vesting_engine.load_active_grants(db, ctx, membership.id)

    reserved_by_grant = {}
    reservations = []
    if grants:
        reserved_by_grant = await stock_reservations.get_active_reservations_by_grant(
            db, ctx, membership_id=membership.id, grant_ids=[grant.id for grant in grants]
        )
        reservations = await stock_reservations.list_active_reservations(
            db, ctx, membership_id=membership.id
        )

    totals = vesting_engine.aggregate_vesting(grants, as_of_date)
    total_reserved = sum(reserved_by_grant.values())
    total_available_vested = max(totals.total_vested_shares - total_reserved, 0)
    eligibility_totals = vesting_engine.VestingTotals(
        total_granted_shares=totals.total_granted_shares,
        total_vested_shares=totals.total_vested_shares,
        total_unvested_shares=totals.total_unvested_shares,
        next_vesting_event=totals.next_vesting_event,
    )
    eligibility_result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=org_settings,
        totals=eligibility_totals,
        as_of_date=as_of_date,
    )

    grant_summaries = vesting_engine.build_grant_summaries(grants, as_of_date)
    exercise_prices = [summary.exercise_price for summary in grant_summaries]
    exercise_price_min = min(exercise_prices) if exercise_prices else None
    exercise_price_max = max(exercise_prices) if exercise_prices else None
    weighted_avg_exercise_price = None
    total_shares_for_weight = sum(summary.total_shares for summary in grant_summaries)
    if total_shares_for_weight:
        weighted_total = sum(
            summary.exercise_price * Decimal(summary.total_shares) for summary in grant_summaries
        )
        weighted_avg_exercise_price = (weighted_total / Decimal(total_shares_for_weight)).quantize(
            TWOPLACES, rounding=ROUND_HALF_UP
        )

    grant_mix_by_status: dict[str, int] = {}
    grant_mix_by_strategy: dict[str, int] = {}
    for grant in grants:
        status = (grant.status or StockGrantStatus.ACTIVE.value).upper()
        grant_mix_by_status[status] = grant_mix_by_status.get(status, 0) + 1
        strategy = (grant.vesting_strategy or VestingStrategy.SCHEDULED.value).upper()
        grant_mix_by_strategy[strategy] = grant_mix_by_strategy.get(strategy, 0) + 1

    reserved_by_status: dict[str, int] = {}
    for reservation in reservations:
        reserved_by_status[reservation.status] = reserved_by_status.get(reservation.status, 0) + int(
            reservation.shares_reserved
        )
    reserved_share_percent = Decimal("0")
    if totals.total_vested_shares:
        reserved_share_percent = (
            Decimal(total_reserved) / Decimal(totals.total_vested_shares) * Decimal("100")
        ).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

    grants_list: list[SelfGrantSummary] = []
    grant_map = {grant.id: grant for grant in grants}
    for summary in grant_summaries:
        grant = grant_map.get(summary.grant_id)
        next_date, next_shares = _grant_next_vesting(grant, as_of_date) if grant else (None, None)
        grants_list.append(
            SelfGrantSummary(
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
                vesting_strategy=getattr(grant, "vesting_strategy", None),
                status=getattr(grant, "status", None),
                next_vesting_date=next_date,
                next_vesting_shares=next_shares,
            )
        )

    unread_count = await _unread_announcements_count(db, ctx, user_id)
    pending_total, pending_actions = await _pending_actions(db, ctx, user_id)

    # Loan summary
    status_stmt = (
        select(LoanApplication.status, func.count())
        .where(
            LoanApplication.org_id == ctx.org_id,
            LoanApplication.org_membership_id == membership.id,
        )
        .group_by(LoanApplication.status)
    )
    status_rows = (await db.execute(status_stmt)).all()
    status_counts = {row[0]: int(row[1]) for row in status_rows}
    total_apps = sum(status_counts.values())
    active_count = status_counts.get(LoanApplicationStatus.ACTIVE.value, 0)
    completed_count = status_counts.get(LoanApplicationStatus.COMPLETED.value, 0)
    pending_count = status_counts.get(LoanApplicationStatus.SUBMITTED.value, 0) + status_counts.get(
        LoanApplicationStatus.IN_REVIEW.value, 0
    )

    active_stmt = (
        select(LoanApplication)
        .options(
            selectinload(LoanApplication.workflow_stages),
            selectinload(LoanApplication.documents),
        )
        .where(
            LoanApplication.org_id == ctx.org_id,
            LoanApplication.org_membership_id == membership.id,
            LoanApplication.status == LoanApplicationStatus.ACTIVE.value,
        )
        .order_by(LoanApplication.activation_date.desc().nullslast(), LoanApplication.created_at.desc())
        .limit(1)
    )
    active_application = (await db.execute(active_stmt)).scalar_one_or_none()

    active_loan_id = None
    active_status = None
    principal = None
    estimated_monthly_payment = None
    total_payable = None
    total_paid = None
    total_interest_paid = None
    remaining_balance = None
    current_stage_type = None
    current_stage_status = None
    has_share_certificate = None
    has_83b_election = None
    days_until_83b_due = None

    if active_application:
        active_loan_id = active_application.id
        active_status = active_application.status
        principal = _as_decimal(active_application.loan_principal)
        estimated_monthly_payment = _as_decimal(active_application.estimated_monthly_payment)
        total_payable = _as_decimal(active_application.total_payable_amount)

        repayment_stmt = select(
            func.coalesce(func.sum(LoanRepayment.amount), 0),
            func.coalesce(func.sum(LoanRepayment.interest_amount), 0),
        ).where(
            LoanRepayment.org_id == ctx.org_id,
            LoanRepayment.loan_application_id == active_application.id,
        )
        rep_row = (await db.execute(repayment_stmt)).first()
        if rep_row:
            total_paid = _as_decimal(rep_row[0])
            total_interest_paid = _as_decimal(rep_row[1])
        if total_payable is not None and total_paid is not None:
            remaining_balance = max(total_payable - total_paid, Decimal("0"))

        stages = sorted(
            [stage for stage in active_application.workflow_stages if stage.status != "COMPLETED"],
            key=lambda stage: stage.created_at,
        )
        if stages:
            current_stage_type = stages[0].stage_type
            current_stage_status = stages[0].status
        has_share_certificate, has_83b_election, days_until_83b_due = (
            loan_applications._compute_workflow_flags(active_application)
        )

    repayment_history_stmt = (
        select(LoanRepayment.amount, LoanRepayment.payment_date)
        .join(LoanApplication, LoanApplication.id == LoanRepayment.loan_application_id)
        .where(
            LoanRepayment.org_id == ctx.org_id,
            LoanApplication.org_membership_id == membership.id,
        )
        .order_by(LoanRepayment.payment_date.desc(), LoanRepayment.created_at.desc())
        .limit(5)
    )
    repayment_rows = (await db.execute(repayment_history_stmt)).all()
    repayment_history = [
        RepaymentHistoryItem(payment_date=row[1], amount=_as_decimal(row[0])) for row in repayment_rows
    ]
    last_payment_date = repayment_history[0].payment_date if repayment_history else None
    last_payment_amount = repayment_history[0].amount if repayment_history else None

    return SelfDashboardSummary(
        as_of_date=as_of_date,
        attention=SelfDashboardAttention(
            unread_announcements_count=unread_count,
            pending_actions_count=pending_total,
            pending_actions=pending_actions,
        ),
        stock_totals=SelfStockTotals(
            grant_count=len(grants),
            total_granted_shares=totals.total_granted_shares,
            total_vested_shares=totals.total_vested_shares,
            total_unvested_shares=totals.total_unvested_shares,
            total_reserved_shares=total_reserved,
            total_available_vested_shares=total_available_vested,
            exercise_price_min=exercise_price_min,
            exercise_price_max=exercise_price_max,
            weighted_avg_exercise_price=weighted_avg_exercise_price,
        ),
        stock_eligibility=eligibility_result,
        vesting_timeline=SelfVestingTimeline(
            next_vesting_date=totals.next_vesting_event.vest_date if totals.next_vesting_event else None,
            next_vesting_shares=totals.next_vesting_event.shares if totals.next_vesting_event else None,
            upcoming_events=vesting_engine.upcoming_vesting_events(grants, as_of_date, limit=6),
            vested_by_month=_build_vested_by_month(grants, as_of_date, months=6),
        ),
        grant_mix=SelfGrantMix(
            by_status=grant_mix_by_status,
            by_vesting_strategy=grant_mix_by_strategy,
        ),
        reservations=SelfStockReservations(
            reserved_share_percent_of_vested=reserved_share_percent,
            reserved_by_status=reserved_by_status,
            reservations_active=[
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
        ),
        grants=grants_list,
        grants_total=len(grants_list),
        policy_snapshot=StockPolicySnapshot(
            min_vested_shares_to_exercise=org_settings.min_vested_shares_to_exercise,
            enforce_min_vested_to_exercise=org_settings.enforce_min_vested_to_exercise,
            min_service_duration_years=org_settings.min_service_duration_years,
            enforce_service_duration_rule=org_settings.enforce_service_duration_rule,
        ),
        loan_summary=SelfLoanSummary(
            total_loan_applications=total_apps,
            active_loans_count=active_count,
            completed_loans_count=completed_count,
            pending_loans_count=pending_count,
            active_loan_id=active_loan_id,
            status=active_status,
            principal=principal,
            estimated_monthly_payment=estimated_monthly_payment,
            total_payable=total_payable,
            total_paid=total_paid,
            total_interest_paid=total_interest_paid,
            remaining_balance=remaining_balance,
            current_stage_type=current_stage_type,
            current_stage_status=current_stage_status,
            has_share_certificate=has_share_certificate,
            has_83b_election=has_83b_election,
            days_until_83b_due=days_until_83b_due,
        ),
        repayment_activity=SelfLoanRepaymentActivity(
            last_payment_date=last_payment_date,
            last_payment_amount=last_payment_amount,
            repayment_history=repayment_history,
        ),
    )
