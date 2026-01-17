from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.department import Department
from app.models.loan_application import LoanApplication
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.models.org_membership import OrgMembership
from app.models.user import User
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
    assigned_to_user_id: UUID | None = None,
) -> tuple[list[tuple], int]:
    conditions = [
        LoanApplication.org_id == ctx.org_id,
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == LoanApplication.id,
        LoanWorkflowStage.stage_type == stage_type,
        LoanWorkflowStage.status != "COMPLETED",
        LoanApplication.status.in_(QUEUE_STATUSES),
    ]
    if assigned_to_user_id is not None:
        conditions.append(LoanWorkflowStage.assigned_to_user_id == assigned_to_user_id)

    count_stmt = (
        select(func.count())
        .select_from(LoanApplication)
        .join(LoanWorkflowStage, LoanWorkflowStage.loan_application_id == LoanApplication.id)
        .where(*conditions)
    )
    count_result = await db.execute(count_stmt)
    total = int(count_result.scalar_one() or 0)

    assigned_user = aliased(User)
    stmt = (
        select(
            LoanApplication,
            OrgMembership,
            User,
            Department,
            LoanWorkflowStage.stage_type,
            LoanWorkflowStage.status,
            assigned_user,
            LoanWorkflowStage.assigned_at,
        )
        .join(LoanWorkflowStage, LoanWorkflowStage.loan_application_id == LoanApplication.id)
        .join(OrgMembership, OrgMembership.id == LoanApplication.org_membership_id)
        .join(User, User.id == OrgMembership.user_id)
        .outerjoin(Department, Department.id == OrgMembership.department_id)
        .outerjoin(assigned_user, assigned_user.id == LoanWorkflowStage.assigned_to_user_id)
        .where(*conditions)
        .order_by(LoanApplication.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    rows = result.all()
    return rows, total
