from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.audit_log import AuditLog
from app.models.loan_application import LoanApplication
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import LoanApplicationStatus
from app.services import stock_reservations


CORE_STAGE_TYPES = {"HR_REVIEW", "FINANCE_PROCESSING", "LEGAL_EXECUTION"}


POST_ACTIVATION_STAGES: list[tuple[str, str]] = [
    ("LEGAL_POST_ISSUANCE", "LEGAL"),
    ("BORROWER_83B_ELECTION", "BORROWER"),
]


async def try_activate_loan(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application: LoanApplication,
    *,
    actor_id=None,
) -> bool:
    if application.status == LoanApplicationStatus.ACTIVE.value:
        return False
    if application.status not in {
        LoanApplicationStatus.SUBMITTED.value,
        LoanApplicationStatus.IN_REVIEW.value,
    }:
        return False

    stage_stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == application.id,
        LoanWorkflowStage.stage_type.in_(CORE_STAGE_TYPES),
    )
    stage_result = await db.execute(stage_stmt)
    stages = stage_result.scalars().all()
    if not stages:
        return False

    statuses = {stage.stage_type: stage.status for stage in stages}
    if not all(statuses.get(stage) == "COMPLETED" for stage in CORE_STAGE_TYPES):
        return False

    old_status = application.status
    application.status = LoanApplicationStatus.ACTIVE.value
    now = datetime.now(timezone.utc)
    application.activation_date = now
    application.election_83b_due_date = (now + timedelta(days=30)).date()

    db.add(application)
    await stock_reservations.set_reservation_status_for_application(
        db,
        ctx,
        application_id=application.id,
        status=LoanApplicationStatus.ACTIVE.value,
    )
    db.add(
        AuditLog(
            org_id=ctx.org_id,
            actor_id=actor_id,
            action="loan_application.activated",
            resource_type="loan_application",
            resource_id=str(application.id),
            old_value={"status": old_status},
            new_value={
                "status": application.status,
                "activation_date": application.activation_date.isoformat(),
                "election_83b_due_date": application.election_83b_due_date.isoformat(),
            },
        )
    )
    await _ensure_post_activation_stages(db, ctx, application)
    return True


async def _ensure_post_activation_stages(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application: LoanApplication,
) -> None:
    if not application.id:
        return
    stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == application.id,
        LoanWorkflowStage.stage_type.in_([stage for stage, _ in POST_ACTIVATION_STAGES]),
    )
    result = await db.execute(stmt)
    existing = {stage.stage_type for stage in result.scalars().all()}
    for stage_type, role_hint in POST_ACTIVATION_STAGES:
        if stage_type in existing:
            continue
        db.add(
            LoanWorkflowStage(
                org_id=ctx.org_id,
                loan_application_id=application.id,
                stage_type=stage_type,
                status="PENDING",
                assigned_role_hint=role_hint,
            )
        )
