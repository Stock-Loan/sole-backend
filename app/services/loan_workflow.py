from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.audit_log import AuditLog
from app.models.loan_application import LoanApplication
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import LoanApplicationStatus
from app.services import stock_reservations
from app.services.audit import model_snapshot, record_audit_log


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
    # Ensure pending stage updates are visible with autoflush disabled.
    await db.flush()
    if application.status == LoanApplicationStatus.ACTIVE.value:
        return False
    if application.status not in {
        LoanApplicationStatus.SUBMITTED.value,
        LoanApplicationStatus.IN_REVIEW.value,
        "PENDING",
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


async def activate_backlog(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    loan_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    actor_id=None,
) -> tuple[int, int, list[str], list[str]]:
    conditions = [LoanApplication.org_id == ctx.org_id]
    if loan_id:
        conditions.append(LoanApplication.id == loan_id)
    else:
        conditions.append(
            LoanApplication.status.in_(
                [
                    LoanApplicationStatus.SUBMITTED.value,
                    LoanApplicationStatus.IN_REVIEW.value,
                    "PENDING",
                    LoanApplicationStatus.ACTIVE.value,
                ]
            )
        )
    stmt = select(LoanApplication).where(*conditions).order_by(LoanApplication.created_at.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    if offset:
        stmt = stmt.offset(offset)

    applications = (await db.execute(stmt)).scalars().all()
    activated_ids: list[str] = []
    post_issuance_completed_ids: list[str] = []
    for application in applications:
        if await try_activate_loan(db, ctx, application, actor_id=actor_id):
            activated_ids.append(str(application.id))
        if await _backfill_post_issuance_stage(db, ctx, application, actor_id=actor_id):
            post_issuance_completed_ids.append(str(application.id))

    if activated_ids or post_issuance_completed_ids:
        await db.commit()
    return len(applications), len(activated_ids), activated_ids, post_issuance_completed_ids


async def _backfill_post_issuance_stage(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application: LoanApplication,
    *,
    actor_id=None,
) -> bool:
    if not application.id:
        return False

    doc_stmt = select(LoanDocument).where(
        LoanDocument.org_id == ctx.org_id,
        LoanDocument.loan_application_id == application.id,
        LoanDocument.document_type == "SHARE_CERTIFICATE",
    )
    document = (await db.execute(doc_stmt)).scalar_one_or_none()
    if not document:
        return False

    stage_stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == application.id,
        LoanWorkflowStage.stage_type == "LEGAL_POST_ISSUANCE",
    )
    stage = (await db.execute(stage_stmt)).scalar_one_or_none()
    if not stage:
        stage = LoanWorkflowStage(
            org_id=ctx.org_id,
            loan_application_id=application.id,
            stage_type="LEGAL_POST_ISSUANCE",
            status="PENDING",
            assigned_role_hint="LEGAL",
        )
        db.add(stage)

    if stage.status == "COMPLETED":
        return False

    old_stage = model_snapshot(stage)
    stage.status = "COMPLETED"
    stage.completed_at = datetime.now(timezone.utc)
    stage.completed_by_user_id = actor_id
    record_audit_log(
        db,
        ctx,
        actor_id=actor_id,
        action="loan_workflow_stage.updated",
        resource_type="loan_workflow_stage",
        resource_id=str(stage.id),
        old_value=old_stage,
        new_value=model_snapshot(stage),
    )
    return True
