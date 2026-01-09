from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.loan_application import LoanApplication
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import LoanDashboardSummary


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

    return LoanDashboardSummary(
        org_id=ctx.org_id,
        as_of=as_of,
        total_loans=total_loans,
        status_counts=status_counts,
        open_stage_counts=open_stage_counts,
        created_last_30_days=created_last_30_days,
        activated_last_30_days=activated_last_30_days,
    )
