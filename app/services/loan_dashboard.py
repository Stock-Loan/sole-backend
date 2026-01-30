from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.loan_application import LoanApplication
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import LoanDashboardSummary
from app.services import loan_repayments


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def build_dashboard_summary(
    db: AsyncSession,
    ctx: deps.TenantContext,
    as_of_date: date | None = None,
) -> LoanDashboardSummary:
    as_of = as_of_date or date.today()
    as_of_datetime = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
    window_start = as_of_datetime - timedelta(days=30)

    status_stmt = (
        select(LoanApplication.status, func.count())
        .where(LoanApplication.org_id == ctx.org_id)
        .group_by(LoanApplication.status)
    )
    status_rows = (await db.execute(status_stmt)).all()
    status_counts = {row[0]: int(row[1]) for row in status_rows}
    total_loans = sum(status_counts.values())
    total_applications = total_loans
    approved_count = status_counts.get("ACTIVE", 0) + status_counts.get("COMPLETED", 0)
    draft_count = status_counts.get("DRAFT", 0)

    stage_stmt = (
        select(LoanWorkflowStage.stage_type, func.count())
        .where(
            LoanWorkflowStage.org_id == ctx.org_id,
            LoanWorkflowStage.status != "COMPLETED",
        )
        .group_by(LoanWorkflowStage.stage_type)
    )
    stage_rows = (await db.execute(stage_stmt)).all()
    open_stage_counts = {row[0]: int(row[1]) for row in stage_rows}
    pending_hr = open_stage_counts.get("HR_REVIEW", 0)
    pending_finance = open_stage_counts.get("FINANCE_PROCESSING", 0)
    pending_legal = open_stage_counts.get("LEGAL_EXECUTION", 0)

    created_stmt = (
        select(func.count())
        .select_from(LoanApplication)
        .where(
            LoanApplication.org_id == ctx.org_id,
            LoanApplication.created_at >= window_start,
            LoanApplication.created_at <= as_of_datetime,
        )
    )
    created_last_30_days = int((await db.execute(created_stmt)).scalar_one() or 0)

    activated_stmt = (
        select(func.count())
        .select_from(LoanApplication)
        .where(
            LoanApplication.org_id == ctx.org_id,
            LoanApplication.activation_date.is_not(None),
            LoanApplication.activation_date >= window_start,
            LoanApplication.activation_date <= as_of_datetime,
        )
    )
    activated_last_30_days = int((await db.execute(activated_stmt)).scalar_one() or 0)

    active_aggregate_stmt = select(
        func.coalesce(func.sum(LoanApplication.loan_principal), 0),
        func.coalesce(func.sum(LoanApplication.shares_to_exercise), 0),
    ).where(
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.status == "ACTIVE",
    )
    active_principal_sum, active_shares_sum = (await db.execute(active_aggregate_stmt)).first()
    active_loan_principal_sum = _as_decimal(active_principal_sum)
    active_loan_total_shares = int(active_shares_sum or 0)

    completed_shares_stmt = select(
        func.coalesce(func.sum(LoanApplication.shares_to_exercise), 0)
    ).where(
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.status == "COMPLETED",
    )
    completed_loan_total_shares = int((await db.execute(completed_shares_stmt)).scalar_one() or 0)

    paid_sum, interest_earned_total = await loan_repayments.sum_repayments_for_org(
        db,
        ctx,
        statuses=["ACTIVE", "COMPLETED"],
    )

    total_due_stmt = select(func.coalesce(func.sum(LoanApplication.total_payable_amount), 0)).where(
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.status.in_(["ACTIVE", "COMPLETED"]),
    )
    total_due = _as_decimal((await db.execute(total_due_stmt)).scalar_one() or 0)
    sum_amount_paid = paid_sum
    sum_amount_owed = total_due - sum_amount_paid
    if sum_amount_owed < Decimal("0"):
        sum_amount_owed = Decimal("0")

    active_fixed_stmt = select(func.count()).where(
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.status == "ACTIVE",
        LoanApplication.interest_type == "FIXED",
    )
    active_variable_stmt = select(func.count()).where(
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.status == "ACTIVE",
        LoanApplication.interest_type == "VARIABLE",
    )
    active_balloon_stmt = select(func.count()).where(
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.status == "ACTIVE",
        LoanApplication.repayment_method == "BALLOON",
    )
    active_principal_and_interest_stmt = select(func.count()).where(
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.status == "ACTIVE",
        LoanApplication.repayment_method == "PRINCIPAL_AND_INTEREST",
    )
    active_fixed_count = int((await db.execute(active_fixed_stmt)).scalar_one() or 0)
    active_variable_count = int((await db.execute(active_variable_stmt)).scalar_one() or 0)
    active_balloon_count = int((await db.execute(active_balloon_stmt)).scalar_one() or 0)
    active_principal_and_interest_count = int(
        (await db.execute(active_principal_and_interest_stmt)).scalar_one() or 0
    )

    return LoanDashboardSummary(
        org_id=ctx.org_id,
        as_of=as_of,
        total_loans=total_loans,
        status_counts=status_counts,
        open_stage_counts=open_stage_counts,
        created_last_30_days=created_last_30_days,
        activated_last_30_days=activated_last_30_days,
        total_applications=total_applications,
        approved_count=approved_count,
        draft_count=draft_count,
        active_loan_principal_sum=active_loan_principal_sum,
        sum_amount_paid=sum_amount_paid,
        sum_amount_owed=sum_amount_owed,
        interest_earned_total=interest_earned_total,
        active_loan_total_shares=active_loan_total_shares,
        completed_loan_total_shares=completed_loan_total_shares,
        pending_hr=pending_hr,
        pending_finance=pending_finance,
        pending_legal=pending_legal,
        active_fixed_count=active_fixed_count,
        active_variable_count=active_variable_count,
        active_balloon_count=active_balloon_count,
        active_principal_and_interest_count=active_principal_and_interest_count,
    )
