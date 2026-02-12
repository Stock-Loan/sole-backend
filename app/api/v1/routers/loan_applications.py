from uuid import UUID

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.models.loan_application import LoanApplication
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.schemas.loan import (
    LoanApplicationDraftCreate,
    LoanApplicationDraftUpdate,
    LoanApplicationSelfDTO,
    LoanApplicationSelfListResponse,
    LoanApplicationSelfSummaryDTO,
    LoanApplicationStatus,
    LoanStageAssigneeSummaryDTO,
    LoanWorkflowStageStatus,
)
from app.schemas.settings import MfaEnforcementAction
from app.services import loan_applications, loan_quotes, loan_workflow

router = APIRouter(prefix="/me/loan-applications", tags=["loan-applications"])


async def _get_membership_or_404(db: AsyncSession, ctx: deps.TenantContext, user_id):
    membership = await loan_applications.get_membership_for_user(db, ctx, user_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    return membership


def _current_stage_from_workflow(stages):
    if not stages:
        return None, None
    for stage in stages:
        if str(stage.status) != LoanWorkflowStageStatus.COMPLETED.value:
            return stage.stage_type, stage.status
    return None, None


def _build_self_payload(
    application: LoanApplication,
    *,
    last_edit_note: str | None = None,
    last_edited_at: datetime | None = None,
    last_edited_by: LoanStageAssigneeSummaryDTO | None = None,
) -> LoanApplicationSelfDTO:
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(
        application
    )
    current_stage_type, current_stage_status = _current_stage_from_workflow(
        application.workflow_stages or []
    )
    return LoanApplicationSelfDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "current_stage_type": current_stage_type,
            "current_stage_status": current_stage_status,
            "last_edit_note": last_edit_note,
            "last_edited_at": last_edited_at,
            "last_edited_by": last_edited_by,
            "workflow_stages": [
                {
                    "stage_type": stage.stage_type,
                    "status": stage.status,
                    "created_at": stage.created_at,
                    "updated_at": stage.updated_at,
                    "completed_at": stage.completed_at,
                }
                for stage in (application.workflow_stages or [])
            ],
            "documents": [
                {
                    "document_type": doc.document_type,
                    "file_name": doc.file_name,
                    "storage_path_or_url": doc.storage_path_or_url,
                    "uploaded_at": doc.uploaded_at,
                }
                for doc in (application.documents or [])
            ],
        }
    )


async def _fetch_last_edit_note(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application_id: UUID,
) -> tuple[str | None, datetime | None, LoanStageAssigneeSummaryDTO | None]:
    actor_membership = aliased(OrgMembership)
    actor_profile = aliased(OrgUserProfile)
    stmt = (
        select(AuditLog, User, actor_profile)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .outerjoin(
            actor_membership,
            (actor_membership.user_id == User.id) & (actor_membership.org_id == ctx.org_id),
        )
        .outerjoin(
            actor_profile,
            (actor_profile.membership_id == actor_membership.id)
            & (actor_profile.org_id == actor_membership.org_id),
        )
        .where(
            AuditLog.org_id == ctx.org_id,
            AuditLog.resource_type == "loan_application",
            AuditLog.resource_id == str(application_id),
            AuditLog.action == "loan_application.admin_edit",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if not row:
        return None, None, None
    audit, actor, actor_profile_row = row
    note = None
    if isinstance(audit.new_value, dict):
        note = audit.new_value.get("edit_note")
    editor = None
    if actor is not None:
        editor_name = (
            actor_profile_row.full_name
            if actor_profile_row and actor_profile_row.full_name
            else actor.email
        )
        editor = LoanStageAssigneeSummaryDTO(
            user_id=actor.id,
            full_name=editor_name,
            email=actor.email,
        )
    return note, audit.created_at, editor


@router.get(
    "",
    response_model=LoanApplicationSelfListResponse,
    summary="List loan applications for the current user",
)
async def list_loan_applications(
    current_user: User = Depends(deps.require_permission(PermissionCode.LOAN_VIEW_OWN)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    status_filter: list[LoanApplicationStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
) -> LoanApplicationSelfListResponse:
    membership = await _get_membership_or_404(db, ctx, current_user.id)
    conditions = [
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.org_membership_id == membership.id,
    ]
    if status_filter:
        status_values = [value.value for value in status_filter]
        conditions.append(LoanApplication.status.in_(status_values))
    if created_from:
        conditions.append(LoanApplication.created_at >= created_from)
    if created_to:
        conditions.append(LoanApplication.created_at <= created_to)

    stage_subq = (
        select(
            LoanWorkflowStage.loan_application_id.label("loan_id"),
            LoanWorkflowStage.stage_type.label("stage_type"),
            LoanWorkflowStage.status.label("stage_status"),
        )
        .where(
            LoanWorkflowStage.org_id == ctx.org_id,
            LoanWorkflowStage.status != "COMPLETED",
        )
        .order_by(LoanWorkflowStage.loan_application_id, LoanWorkflowStage.created_at)
        .distinct(LoanWorkflowStage.loan_application_id)
        .subquery()
    )

    count_stmt = select(func.count()).select_from(LoanApplication).where(*conditions)
    count_result = await db.execute(count_stmt)
    total = int(count_result.scalar_one())

    stmt = (
        select(
            LoanApplication,
            stage_subq.c.stage_type,
            stage_subq.c.stage_status,
        )
        .outerjoin(stage_subq, stage_subq.c.loan_id == LoanApplication.id)
        .where(*conditions)
        .order_by(LoanApplication.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    rows = result.all()
    return LoanApplicationSelfListResponse(
        items=[
            LoanApplicationSelfSummaryDTO(
                id=app.id,
                status=app.status,
                as_of_date=app.as_of_date,
                shares_to_exercise=app.shares_to_exercise,
                loan_principal=app.loan_principal,
                estimated_monthly_payment=app.estimated_monthly_payment,
                total_payable_amount=app.total_payable_amount,
                interest_type=app.interest_type,
                repayment_method=app.repayment_method,
                term_months=app.term_months,
                current_stage_type=stage_type,
                current_stage_status=stage_status,
                created_at=app.created_at,
                updated_at=app.updated_at,
            )
            for app, stage_type, stage_status in rows
        ],
        total=total,
    )


@router.get(
    "/{application_id}",
    response_model=LoanApplicationSelfDTO,
    summary="Get a loan application by id",
)
async def get_loan_application(
    application_id: UUID,
    current_user: User = Depends(deps.require_permission(PermissionCode.LOAN_VIEW_OWN)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanApplicationSelfDTO:
    membership = await _get_membership_or_404(db, ctx, current_user.id)
    application = await loan_applications.get_application_with_related(
        db, ctx, application_id, membership_id=membership.id
    )
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    activated = await loan_workflow.try_activate_loan(db, ctx, application)
    if activated:
        await db.commit()
        refreshed = await loan_applications.get_application_with_related(
            db, ctx, application.id, membership_id=membership.id
        )
        if not refreshed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
            )
        application = refreshed
    last_edit_note, last_edited_at, last_edited_by = await _fetch_last_edit_note(
        db, ctx, application.id
    )
    return _build_self_payload(
        application,
        last_edit_note=last_edit_note,
        last_edited_at=last_edited_at,
        last_edited_by=last_edited_by,
    )


@router.post(
    "",
    response_model=LoanApplicationSelfDTO,
    status_code=201,
    summary="Create a draft loan application",
)
async def create_loan_application(
    payload: LoanApplicationDraftCreate,
    current_user: User = Depends(deps.require_permission(PermissionCode.LOAN_APPLY)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LoanApplicationSelfDTO:
    membership = await _get_membership_or_404(db, ctx, current_user.id)
    try:
        application = await loan_applications.create_draft_application(
            db,
            ctx,
            membership,
            payload,
            actor_id=current_user.id,
            idempotency_key=idempotency_key,
        )
    except loan_quotes.LoanQuoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    await db.commit()
    hydrated = await loan_applications.get_application_with_related(
        db, ctx, application.id, membership_id=membership.id
    )
    if hydrated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    return _build_self_payload(hydrated)


@router.patch(
    "/{application_id}",
    response_model=LoanApplicationSelfDTO,
    summary="Update a draft loan application",
)
async def update_loan_application(
    application_id: UUID,
    payload: LoanApplicationDraftUpdate,
    current_user: User = Depends(deps.require_permission(PermissionCode.LOAN_APPLY)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanApplicationSelfDTO:
    membership = await _get_membership_or_404(db, ctx, current_user.id)
    stmt = select(LoanApplication).where(
        LoanApplication.id == application_id,
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.org_membership_id == membership.id,
    )
    result = await db.execute(stmt)
    application = result.scalar_one_or_none()
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    try:
        updated = await loan_applications.update_draft_application(
            db,
            ctx,
            membership,
            application,
            payload,
            actor_id=current_user.id,
        )
    except loan_quotes.LoanQuoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    except StaleDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "concurrent_update",
                "message": "The loan application was updated by another request. Please refresh and retry.",
                "details": {},
            },
        ) from exc
    await db.commit()
    hydrated = await loan_applications.get_application_with_related(
        db, ctx, updated.id, membership_id=membership.id
    )
    if hydrated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    return _build_self_payload(hydrated)


@router.post(
    "/{application_id}/submit",
    response_model=LoanApplicationSelfDTO,
    summary="Submit a draft loan application",
)
async def submit_loan_application(
    application_id: UUID,
    current_user: User = Depends(
        deps.require_permission_with_mfa(
            PermissionCode.LOAN_APPLY,
            action=MfaEnforcementAction.LOAN_SUBMISSION.value,
        )
    ),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LoanApplicationSelfDTO:
    membership = await _get_membership_or_404(db, ctx, current_user.id)
    stmt = select(LoanApplication).where(
        LoanApplication.id == application_id,
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.org_membership_id == membership.id,
    )
    result = await db.execute(stmt)
    application = result.scalar_one_or_none()
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    try:
        submitted = await loan_applications.submit_application(
            db,
            ctx,
            membership,
            application,
            current_user,
            actor_id=current_user.id,
            idempotency_key=idempotency_key,
        )
    except loan_quotes.LoanQuoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    except StaleDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "concurrent_update",
                "message": "The loan application was updated by another request. Please refresh and retry.",
                "details": {},
            },
        ) from exc
    await db.commit()
    hydrated = await loan_applications.get_application_with_related(
        db, ctx, submitted.id, membership_id=membership.id
    )
    if hydrated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    return _build_self_payload(hydrated)


@router.post(
    "/{application_id}/cancel",
    response_model=LoanApplicationSelfDTO,
    summary="Cancel a draft loan application",
)
async def cancel_loan_application(
    application_id: UUID,
    current_user: User = Depends(deps.require_permission(PermissionCode.LOAN_APPLY)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanApplicationSelfDTO:
    membership = await _get_membership_or_404(db, ctx, current_user.id)
    stmt = select(LoanApplication).where(
        LoanApplication.id == application_id,
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.org_membership_id == membership.id,
    )
    result = await db.execute(stmt)
    application = result.scalar_one_or_none()
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    try:
        cancelled = await loan_applications.cancel_draft_application(
            db, ctx, application, actor_id=current_user.id
        )
    except loan_quotes.LoanQuoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    except StaleDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "concurrent_update",
                "message": "The loan application was updated by another request. Please refresh and retry.",
                "details": {},
            },
        ) from exc
    await db.commit()
    hydrated = await loan_applications.get_application_with_related(
        db, ctx, cancelled.id, membership_id=membership.id
    )
    if hydrated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    return _build_self_payload(hydrated)
