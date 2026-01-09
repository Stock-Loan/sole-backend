from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.loan_application import LoanApplication
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import LoanApplicationStatus


QUEUE_STATUSES = {
    LoanApplicationStatus.SUBMITTED.value,
    LoanApplicationStatus.IN_REVIEW.value,
}


async def list_queue(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    stage_type: str,
    limit: int,
    offset: int,
) -> tuple[list[LoanApplication], int]:
    conditions = [
        LoanApplication.org_id == ctx.org_id,
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == LoanApplication.id,
        LoanWorkflowStage.stage_type == stage_type,
        LoanWorkflowStage.status != "COMPLETED",
        LoanApplication.status.in_(QUEUE_STATUSES),
    ]

    count_stmt = (
        select(func.count())
        .select_from(LoanApplication)
        .join(LoanWorkflowStage, LoanWorkflowStage.loan_application_id == LoanApplication.id)
        .where(*conditions)
    )
    count_result = await db.execute(count_stmt)
    total = int(count_result.scalar_one() or 0)

    stmt = (
        select(LoanApplication)
        .join(LoanWorkflowStage, LoanWorkflowStage.loan_application_id == LoanApplication.id)
        .where(*conditions)
        .order_by(LoanApplication.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    applications = result.scalars().all()
    return applications, total
