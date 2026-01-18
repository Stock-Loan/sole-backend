from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.core.settings import settings
from app.db.session import get_db
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.schemas.loan import (
    LoanAdminUpdateRequest,
    LoanApplicationDTO,
    LoanApplicationListResponse,
    LoanApplicationSummaryDTO,
    LoanActivationMaintenanceResponse,
    LoanApplicantSummaryDTO,
    LoanStageAssigneeSummaryDTO,
    LoanApplicationStatus,
    LoanDocumentCreateRequest,
    LoanDocumentGroup,
    LoanDocumentListResponse,
    LoanDocumentDTO,
    LoanDocumentType,
    LoanRepaymentCreateRequest,
    LoanRepaymentDTO,
    LoanRepaymentListResponse,
    LoanFinanceReviewResponse,
    LoanHRReviewResponse,
    LoanLegalReviewResponse,
    LoanScheduleResponse,
    LoanScheduleWhatIfRequest,
    LoanWorkflowStageType,
    LoanWorkflowStageDTO,
    LoanWorkflowStageAssignRequest,
    LoanWorkflowStageStatus,
    LoanWorkflowStageUpdateRequest,
)
from app.services import authz, loan_applications, loan_exports, loan_queue, loan_repayments, loan_schedules, loan_workflow, stock_summary
from app.services.audit import model_snapshot, record_audit_log
from app.services.local_uploads import resolve_local_path, save_upload


router = APIRouter(prefix="/org/loans", tags=["loan-admin"])


CORE_QUEUE_STAGE_TYPES = {
    LoanWorkflowStageType.HR_REVIEW,
    LoanWorkflowStageType.FINANCE_PROCESSING,
    LoanWorkflowStageType.LEGAL_EXECUTION,
}


async def _save_local_document(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id: UUID,
    document_type: LoanDocumentType,
    stage_type: str,
    file: UploadFile,
    actor_id: UUID,
) -> LoanDocument:
    base_dir = Path(settings.local_upload_dir)
    relative_path, original_name = await save_upload(
        file,
        base_dir=base_dir,
        subdir=Path("loan-documents") / ctx.org_id / str(loan_id),
    )
    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type=stage_type,
        document_type=document_type.value,
        file_name=original_name,
        storage_path_or_url=relative_path,
        uploaded_by_user_id=actor_id,
    )
    db.add(document)
    record_audit_log(
        db,
        ctx,
        actor_id=actor_id,
        action="loan_document.created",
        resource_type="loan_document",
        resource_id=str(document.id),
        old_value=None,
        new_value=model_snapshot(document),
    )
    await db.commit()
    await db.refresh(document)
    return document


def _stage_manage_permission(stage_type: LoanWorkflowStageType) -> PermissionCode:
    if stage_type == LoanWorkflowStageType.HR_REVIEW:
        return PermissionCode.LOAN_WORKFLOW_HR_MANAGE
    if stage_type == LoanWorkflowStageType.FINANCE_PROCESSING:
        return PermissionCode.LOAN_WORKFLOW_FINANCE_MANAGE
    if stage_type == LoanWorkflowStageType.LEGAL_EXECUTION:
        return PermissionCode.LOAN_WORKFLOW_LEGAL_MANAGE
    raise ValueError(f"Unsupported stage type: {stage_type}")


def _build_applicant_summary(membership, user, department) -> LoanApplicantSummaryDTO:
    return LoanApplicantSummaryDTO(
        org_membership_id=membership.id,
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        employee_id=membership.employee_id,
        department_id=membership.department_id,
        department_name=department.name if department else None,
    )


def _build_admin_summary(row) -> LoanApplicationSummaryDTO:
    application, membership, user, department, stage_type, stage_status, assigned_user, assigned_at = row
    applicant = _build_applicant_summary(membership, user, department)
    assignee = None
    if assigned_user is not None:
        assignee = LoanStageAssigneeSummaryDTO(
            user_id=assigned_user.id,
            full_name=assigned_user.full_name,
            email=assigned_user.email,
        )
    return LoanApplicationSummaryDTO(
        id=application.id,
        org_membership_id=membership.id,
        applicant=applicant,
        status=application.status,
        version=application.version,
        as_of_date=application.as_of_date,
        shares_to_exercise=application.shares_to_exercise,
        total_exercisable_shares_snapshot=application.total_exercisable_shares_snapshot,
        purchase_price=application.purchase_price,
        down_payment_amount=application.down_payment_amount,
        loan_principal=application.loan_principal,
        estimated_monthly_payment=application.estimated_monthly_payment,
        total_payable_amount=application.total_payable_amount,
        interest_type=application.interest_type,
        repayment_method=application.repayment_method,
        term_months=application.term_months,
        current_stage_type=stage_type,
        current_stage_status=stage_status,
        current_stage_assignee=assignee,
        current_stage_assigned_at=assigned_at,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


async def _fetch_applicant_summary(db: AsyncSession, ctx: deps.TenantContext, application) -> LoanApplicantSummaryDTO | None:
    membership_bundle = await loan_applications.get_membership_with_user(
        db, ctx, application.org_membership_id
    )
    if not membership_bundle:
        return None
    membership, user, department = membership_bundle
    return _build_applicant_summary(membership, user, department)


@router.get(
    "",
    response_model=LoanApplicationListResponse,
    summary="List loan applications for the org",
)
async def list_loans(
    statuses: list[LoanApplicationStatus] | None = Query(default=None, alias="status"),
    stage_type: LoanWorkflowStageType | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_VIEW_ALL)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanApplicationListResponse:
    if created_from and created_to and created_from > created_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_date_range",
                "message": "created_from must be earlier than created_to",
                "details": {"created_from": created_from.isoformat(), "created_to": created_to.isoformat()},
            },
        )

    applications, total = await loan_applications.list_admin_applications(
        db,
        ctx,
        limit=limit,
        offset=offset,
        statuses=statuses,
        stage_type=stage_type.value if stage_type else None,
        created_from=created_from,
        created_to=created_to,
    )
    return LoanApplicationListResponse(
        items=[_build_admin_summary(row) for row in applications],
        total=total,
    )


@router.post(
    "/maintenance/activate-backlog",
    response_model=LoanActivationMaintenanceResponse,
    summary="Activate backlog loans with completed core stages",
)
async def activate_loan_backlog(
    loan_id: UUID | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_VIEW_ALL)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanActivationMaintenanceResponse:
    checked, activated, activated_ids, post_issuance_completed_ids = await loan_workflow.activate_backlog(
        db,
        ctx,
        loan_id=str(loan_id) if loan_id else None,
        limit=limit,
        offset=offset,
        actor_id=current_user.id,
    )
    if loan_id and checked == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found")
    return LoanActivationMaintenanceResponse(
        checked=checked,
        activated=activated,
        skipped=checked - activated,
        activated_ids=[UUID(value) for value in activated_ids],
        post_issuance_completed=len(post_issuance_completed_ids),
        post_issuance_completed_ids=[UUID(value) for value in post_issuance_completed_ids],
    )


@router.get(
    "/{loan_id}",
    response_model=LoanApplicationDTO,
    summary="Get loan application detail",
)
async def get_loan(
    loan_id: UUID,
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_VIEW_ALL)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanApplicationDTO:
    application = await loan_applications.get_application_with_related(db, ctx, loan_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found")
    activated = await loan_workflow.try_activate_loan(db, ctx, application)
    if activated:
        await db.commit()
        await db.refresh(application)
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(application)
    applicant = await _fetch_applicant_summary(db, ctx, application)
    return LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
        }
    )


@router.get(
    "/{loan_id}/documents",
    response_model=LoanDocumentListResponse,
    summary="List loan documents",
)
async def list_loan_documents(
    loan_id: UUID,
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentListResponse:
    await _get_application_or_404(db, ctx, loan_id)
    stmt = select(LoanDocument).options(selectinload(LoanDocument.uploaded_by_user)).where(
        LoanDocument.org_id == ctx.org_id,
        LoanDocument.loan_application_id == loan_id,
    ).order_by(LoanDocument.uploaded_at.desc())
    documents = (await db.execute(stmt)).scalars().all()

    grouped: dict[str, list[LoanDocumentDTO]] = {}
    for document in documents:
        grouped.setdefault(document.stage_type, []).append(LoanDocumentDTO.model_validate(document))

    groups = [
        LoanDocumentGroup(stage_type=stage_type, documents=items)
        for stage_type, items in sorted(grouped.items())
    ]
    return LoanDocumentListResponse(
        loan_id=loan_id,
        total=len(documents),
        groups=groups,
    )


@router.get(
    "/{loan_id}/repayments",
    response_model=LoanRepaymentListResponse,
    summary="List loan repayments",
)
async def list_loan_repayments(
    loan_id: UUID,
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_PAYMENT_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanRepaymentListResponse:
    await _get_application_or_404(db, ctx, loan_id)
    repayments = await loan_repayments.list_repayments(db, ctx, loan_id)
    return LoanRepaymentListResponse(
        loan_id=loan_id,
        total=len(repayments),
        items=[LoanRepaymentDTO.model_validate(item) for item in repayments],
    )


@router.post(
    "/{loan_id}/repayments",
    response_model=LoanRepaymentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Record a loan repayment",
)
async def record_loan_repayment(
    loan_id: UUID,
    payload: LoanRepaymentCreateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_PAYMENT_RECORD)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanRepaymentDTO:
    application = await _get_application_or_404(db, ctx, loan_id)
    try:
        repayment = await loan_repayments.record_repayment(
            db,
            ctx,
            application,
            payload,
            actor_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_repayment", "message": str(exc), "details": {}},
        ) from exc
    await db.commit()
    await db.refresh(repayment)
    return LoanRepaymentDTO.model_validate(repayment)


@router.get(
    "/documents/{document_id}/download",
    response_class=FileResponse,
    summary="Download loan document file",
)
async def download_loan_document(
    document_id: UUID,
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    stmt = select(LoanDocument).where(
        LoanDocument.org_id == ctx.org_id,
        LoanDocument.id == document_id,
    )
    document = (await db.execute(stmt)).scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan document not found")
    if document.storage_path_or_url.startswith("http"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "document_not_local",
                "message": "Document is stored externally",
                "details": {"storage_path_or_url": document.storage_path_or_url},
            },
        )
    try:
        file_path = resolve_local_path(Path(settings.local_upload_dir), document.storage_path_or_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_path",
                "message": "Document path is invalid",
                "details": {},
            },
        ) from exc
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "document_missing",
                "message": "Document file does not exist",
                "details": {"storage_path_or_url": document.storage_path_or_url},
            },
        )
    return FileResponse(file_path, filename=document.file_name, media_type="application/octet-stream")


@router.get(
    "/{loan_id}/schedule",
    response_model=LoanScheduleResponse,
    summary="Get loan amortization schedule",
)
async def get_loan_schedule(
    loan_id: UUID,
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_SCHEDULE_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanScheduleResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    try:
        return loan_schedules.build_schedule(application)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_schedule", "message": str(exc), "details": {}},
        ) from exc


@router.post(
    "/{loan_id}/schedule/what-if",
    response_model=LoanScheduleResponse,
    summary="Run loan schedule what-if simulation",
)
async def get_loan_schedule_what_if(
    loan_id: UUID,
    payload: LoanScheduleWhatIfRequest,
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_WHAT_IF_SIMULATE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanScheduleResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    try:
        return loan_schedules.build_schedule_what_if(application, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_schedule", "message": str(exc), "details": {}},
        ) from exc


@router.get(
    "/{loan_id}/schedule/export",
    response_class=StreamingResponse,
    summary="Export loan amortization schedule as CSV",
)
async def export_loan_schedule(
    loan_id: UUID,
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_EXPORT_SCHEDULE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    try:
        schedule = loan_schedules.build_schedule(application)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_schedule", "message": str(exc), "details": {}},
        ) from exc
    content = loan_exports.schedule_to_csv(schedule)
    filename = f"loan_schedule_{loan_id}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch(
    "/{loan_id}",
    response_model=LoanApplicationDTO,
    summary="Update loan application status",
)
async def update_loan(
    loan_id: UUID,
    payload: LoanAdminUpdateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanApplicationDTO:
    if payload.status is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_status",
                "message": "status is required",
                "details": {"field": "status"},
            },
        )
    if payload.status == LoanApplicationStatus.REJECTED and not (payload.decision_reason or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "decision_reason_required",
                "message": "decision_reason is required when rejecting a loan",
                "details": {"field": "decision_reason"},
            },
        )

    application = await loan_applications.get_application_with_related(db, ctx, loan_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found")

    try:
        updated = await loan_applications.update_admin_application(
            db,
            ctx,
            application,
            next_status=payload.status,
            decision_reason=payload.decision_reason,
            actor_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_status_transition", "message": str(exc), "details": {}},
        ) from exc

    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(updated)
    applicant = await _fetch_applicant_summary(db, ctx, updated)
    return LoanApplicationDTO.model_validate(updated).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
        }
    )


@router.get(
    "/queue/hr",
    response_model=LoanApplicationListResponse,
    summary="List loan applications awaiting HR review",
)
async def list_hr_queue(
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_HR_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LoanApplicationListResponse:
    applications, total = await loan_queue.list_queue(
        db, ctx, stage_type="HR_REVIEW", limit=limit, offset=offset
    )
    return LoanApplicationListResponse(
        items=[_build_admin_summary(row) for row in applications],
        total=total,
    )


@router.get(
    "/queue/finance",
    response_model=LoanApplicationListResponse,
    summary="List loan applications awaiting Finance processing",
)
async def list_finance_queue(
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_FINANCE_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LoanApplicationListResponse:
    applications, total = await loan_queue.list_queue(
        db, ctx, stage_type="FINANCE_PROCESSING", limit=limit, offset=offset
    )
    return LoanApplicationListResponse(
        items=[_build_admin_summary(row) for row in applications],
        total=total,
    )


@router.get(
    "/queue/legal",
    response_model=LoanApplicationListResponse,
    summary="List loan applications awaiting Legal execution",
)
async def list_legal_queue(
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_LEGAL_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LoanApplicationListResponse:
    applications, total = await loan_queue.list_queue(
        db, ctx, stage_type="LEGAL_EXECUTION", limit=limit, offset=offset
    )
    return LoanApplicationListResponse(
        items=[_build_admin_summary(row) for row in applications],
        total=total,
    )


@router.get(
    "/queue/me/hr",
    response_model=LoanApplicationListResponse,
    summary="List loan applications assigned to the current user for HR review",
)
async def list_my_hr_queue(
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_HR_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LoanApplicationListResponse:
    applications, total = await loan_queue.list_queue(
        db,
        ctx,
        stage_type="HR_REVIEW",
        limit=limit,
        offset=offset,
        assigned_to_user_id=current_user.id,
    )
    return LoanApplicationListResponse(
        items=[_build_admin_summary(row) for row in applications],
        total=total,
    )


@router.get(
    "/queue/me/finance",
    response_model=LoanApplicationListResponse,
    summary="List loan applications assigned to the current user for Finance processing",
)
async def list_my_finance_queue(
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_FINANCE_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LoanApplicationListResponse:
    applications, total = await loan_queue.list_queue(
        db,
        ctx,
        stage_type="FINANCE_PROCESSING",
        limit=limit,
        offset=offset,
        assigned_to_user_id=current_user.id,
    )
    return LoanApplicationListResponse(
        items=[_build_admin_summary(row) for row in applications],
        total=total,
    )


@router.get(
    "/queue/me/legal",
    response_model=LoanApplicationListResponse,
    summary="List loan applications assigned to the current user for Legal execution",
)
async def list_my_legal_queue(
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_LEGAL_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LoanApplicationListResponse:
    applications, total = await loan_queue.list_queue(
        db,
        ctx,
        stage_type="LEGAL_EXECUTION",
        limit=limit,
        offset=offset,
        assigned_to_user_id=current_user.id,
    )
    return LoanApplicationListResponse(
        items=[_build_admin_summary(row) for row in applications],
        total=total,
    )


@router.post(
    "/{loan_id}/workflow/{stage_type}/assign",
    response_model=LoanWorkflowStageDTO,
    summary="Assign a loan workflow stage",
)
async def assign_workflow_stage(
    loan_id: UUID,
    stage_type: LoanWorkflowStageType,
    payload: LoanWorkflowStageAssignRequest,
    current_user=Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanWorkflowStageDTO:
    if stage_type not in CORE_QUEUE_STAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "unsupported_stage_type",
                "message": "Assignment is only supported for HR, Finance, and Legal stages",
                "details": {"stage_type": stage_type.value},
            },
        )

    required_permission = _stage_manage_permission(stage_type)
    assignee_id = payload.assignee_user_id or current_user.id

    if assignee_id != current_user.id:
        allowed = await authz.check_permission(
            current_user, ctx, PermissionCode.LOAN_WORKFLOW_ASSIGN_ANY, db
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {PermissionCode.LOAN_WORKFLOW_ASSIGN_ANY.value}",
            )
    else:
        allowed = await authz.check_permission(current_user, ctx, required_permission, db)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {required_permission.value}",
            )

    user_stmt = select(User).where(User.id == assignee_id, User.org_id == ctx.org_id)
    user_result = await db.execute(user_stmt)
    assignee = user_result.scalar_one_or_none()
    if not assignee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee user not found")

    membership_stmt = select(OrgMembership.id).where(
        OrgMembership.org_id == ctx.org_id,
        OrgMembership.user_id == assignee_id,
    )
    membership_result = await db.execute(membership_stmt)
    if membership_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee membership not found")

    assignee_allowed = await authz.check_permission(assignee, ctx, required_permission, db)
    if not assignee_allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "assignee_missing_permission",
                "message": "Assignee does not have required workflow permission",
                "details": {"permission": required_permission.value},
            },
        )

    stage_stmt = (
        select(LoanWorkflowStage)
        .where(
            LoanWorkflowStage.org_id == ctx.org_id,
            LoanWorkflowStage.loan_application_id == loan_id,
            LoanWorkflowStage.stage_type == stage_type.value,
        )
        .with_for_update()
    )
    stage_result = await db.execute(stage_stmt)
    stage = stage_result.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow stage not found")
    if stage.status == LoanWorkflowStageStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "stage_completed",
                "message": "Completed workflow stages cannot be reassigned",
                "details": {"stage_type": stage.stage_type},
            },
        )

    old_snapshot = model_snapshot(stage)
    stage.assigned_to_user_id = assignee_id
    stage.assigned_by_user_id = current_user.id
    stage.assigned_at = datetime.now(timezone.utc)
    stage.status = LoanWorkflowStageStatus.IN_PROGRESS.value
    db.add(stage)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_workflow_stage.assigned",
        resource_type="loan_workflow_stage",
        resource_id=str(stage.id),
        old_value=old_snapshot,
        new_value=model_snapshot(stage),
    )
    await db.commit()
    await db.refresh(stage)
    return LoanWorkflowStageDTO.model_validate(stage)


async def _get_application_or_404(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id: UUID,
):
    application = await loan_applications.get_application_with_related(db, ctx, loan_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found")
    return application


async def _get_hr_stage_or_404(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id: UUID,
):
    stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == loan_id,
        LoanWorkflowStage.stage_type == "HR_REVIEW",
    )
    result = await db.execute(stmt)
    stage = result.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HR workflow stage not found")
    return stage


async def _get_finance_stage_or_404(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id: UUID,
):
    stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == loan_id,
        LoanWorkflowStage.stage_type == "FINANCE_PROCESSING",
    )
    result = await db.execute(stmt)
    stage = result.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance workflow stage not found")
    return stage


async def _get_legal_stage_or_404(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id: UUID,
):
    stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == loan_id,
        LoanWorkflowStage.stage_type == "LEGAL_EXECUTION",
    )
    result = await db.execute(stmt)
    stage = result.scalar_one_or_none()
    if not stage:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Legal workflow stage not found")
    return stage


@router.get(
    "/{loan_id}/hr",
    response_model=LoanHRReviewResponse,
    summary="Get HR review details for a loan application",
)
async def get_hr_review(
    loan_id: UUID,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_HR_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanHRReviewResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    try:
        summary = await stock_summary.build_stock_summary(
            db, ctx, application.org_membership_id, application.as_of_date
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    hr_stage = None
    for stage in application.workflow_stages or []:
        if stage.stage_type == "HR_REVIEW":
            hr_stage = stage
            break
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(application)
    applicant = await _fetch_applicant_summary(db, ctx, application)
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
        }
    )
    return LoanHRReviewResponse(
        loan_application=loan_payload,
        stock_summary=summary,
        hr_stage=LoanWorkflowStageDTO.model_validate(hr_stage) if hr_stage else None,
    )


@router.patch(
    "/{loan_id}/hr",
    response_model=LoanWorkflowStageDTO,
    summary="Update HR review stage",
)
async def update_hr_stage(
    loan_id: UUID,
    payload: LoanWorkflowStageUpdateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_WORKFLOW_HR_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanWorkflowStageDTO:
    stage = await _get_hr_stage_or_404(db, ctx, loan_id)
    stage.loan_application = await _get_application_or_404(db, ctx, loan_id)
    old_snapshot = model_snapshot(stage)
    if payload.status not in {LoanWorkflowStageStatus.IN_PROGRESS, LoanWorkflowStageStatus.COMPLETED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_stage_status",
                "message": "HR stage status must be IN_PROGRESS or COMPLETED",
                "details": {"status": payload.status},
            },
        )
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        required_types = {
            LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT.value,
            LoanDocumentType.SPOUSE_PARTNER_CONSENT.value,
        }
        doc_stmt = select(LoanDocument.document_type).where(
            LoanDocument.org_id == ctx.org_id,
            LoanDocument.loan_application_id == loan_id,
            LoanDocument.stage_type == "HR_REVIEW",
            LoanDocument.document_type.in_(required_types),
        )
        doc_result = await db.execute(doc_stmt)
        present = {row[0] for row in doc_result.all()}
        missing = sorted(required_types - present)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "document_required",
                    "message": "All required HR documents must be uploaded before completing HR review",
                    "details": {"missing_document_types": missing},
                },
            )

    stage.status = payload.status.value
    stage.notes = payload.notes
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        stage.completed_at = datetime.now(timezone.utc)
        stage.completed_by_user_id = current_user.id
    else:
        stage.completed_at = None
        stage.completed_by_user_id = None

    db.add(stage)
    await loan_workflow.try_activate_loan(db, ctx, stage.loan_application, actor_id=current_user.id)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_workflow_stage.updated",
        resource_type="loan_workflow_stage",
        resource_id=str(stage.id),
        old_value=old_snapshot,
        new_value=model_snapshot(stage),
    )
    await db.commit()
    await db.refresh(stage)
    return LoanWorkflowStageDTO.model_validate(stage)


@router.post(
    "/{loan_id}/documents/hr",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload HR loan document",
)
async def upload_hr_document(
    loan_id: UUID,
    payload: LoanDocumentCreateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_MANAGE_HR)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    await _get_application_or_404(db, ctx, loan_id)
    allowed_types = {
        LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT,
        LoanDocumentType.SPOUSE_PARTNER_CONSENT,
    }
    if payload.document_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "HR documents must be Notice of Stock Option Grant or Spouse/Partner Consent",
                "details": {"document_type": payload.document_type},
            },
        )
    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type="HR_REVIEW",
        document_type=payload.document_type.value,
        file_name=payload.file_name,
        storage_path_or_url=payload.storage_path_or_url,
        uploaded_by_user_id=current_user.id,
    )
    db.add(document)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_document.created",
        resource_type="loan_document",
        resource_id=str(document.id),
        old_value=None,
        new_value=model_snapshot(document),
    )
    await db.commit()
    await db.refresh(document)
    return LoanDocumentDTO.model_validate(document)


@router.post(
    "/{loan_id}/documents/hr/upload",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload HR loan document (file)",
)
async def upload_hr_document_file(
    loan_id: UUID,
    document_type: LoanDocumentType = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_MANAGE_HR)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    await _get_application_or_404(db, ctx, loan_id)
    allowed_types = {
        LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT,
        LoanDocumentType.SPOUSE_PARTNER_CONSENT,
    }
    if document_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "HR documents must be Notice of Stock Option Grant or Spouse/Partner Consent",
                "details": {"document_type": document_type},
            },
        )
    document = await _save_local_document(
        db=db,
        ctx=ctx,
        loan_id=loan_id,
        document_type=document_type,
        stage_type="HR_REVIEW",
        file=file,
        actor_id=current_user.id,
    )
    return LoanDocumentDTO.model_validate(document)


@router.get(
    "/{loan_id}/finance",
    response_model=LoanFinanceReviewResponse,
    summary="Get Finance view for a loan application",
)
async def get_finance_review(
    loan_id: UUID,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_FINANCE_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanFinanceReviewResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    finance_stage = None
    for stage in application.workflow_stages or []:
        if stage.stage_type == "FINANCE_PROCESSING":
            finance_stage = stage
            break
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(application)
    applicant = await _fetch_applicant_summary(db, ctx, application)
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
        }
    )
    return LoanFinanceReviewResponse(
        loan_application=loan_payload,
        finance_stage=LoanWorkflowStageDTO.model_validate(finance_stage) if finance_stage else None,
    )


@router.patch(
    "/{loan_id}/finance",
    response_model=LoanWorkflowStageDTO,
    summary="Update Finance processing stage",
)
async def update_finance_stage(
    loan_id: UUID,
    payload: LoanWorkflowStageUpdateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_WORKFLOW_FINANCE_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanWorkflowStageDTO:
    stage = await _get_finance_stage_or_404(db, ctx, loan_id)
    stage.loan_application = await _get_application_or_404(db, ctx, loan_id)
    old_snapshot = model_snapshot(stage)
    if payload.status not in {LoanWorkflowStageStatus.IN_PROGRESS, LoanWorkflowStageStatus.COMPLETED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_stage_status",
                "message": "Finance stage status must be IN_PROGRESS or COMPLETED",
                "details": {"status": payload.status},
            },
        )
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        doc_stmt = select(LoanDocument).where(
            LoanDocument.org_id == ctx.org_id,
            LoanDocument.loan_application_id == loan_id,
            LoanDocument.stage_type == "FINANCE_PROCESSING",
            LoanDocument.document_type == LoanDocumentType.PAYMENT_INSTRUCTIONS.value,
        )
        doc_result = await db.execute(doc_stmt)
        required_doc = doc_result.scalar_one_or_none()
        if not required_doc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "document_required",
                    "message": "Payment instructions document is required before completing Finance processing",
                    "details": {"document_type": LoanDocumentType.PAYMENT_INSTRUCTIONS.value},
                },
            )

    stage.status = payload.status.value
    stage.notes = payload.notes
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        stage.completed_at = datetime.now(timezone.utc)
        stage.completed_by_user_id = current_user.id
    else:
        stage.completed_at = None
        stage.completed_by_user_id = None

    db.add(stage)
    await loan_workflow.try_activate_loan(db, ctx, stage.loan_application, actor_id=current_user.id)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_workflow_stage.updated",
        resource_type="loan_workflow_stage",
        resource_id=str(stage.id),
        old_value=old_snapshot,
        new_value=model_snapshot(stage),
    )
    await db.commit()
    await db.refresh(stage)
    return LoanWorkflowStageDTO.model_validate(stage)


@router.post(
    "/{loan_id}/documents/finance",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Finance loan document",
)
async def upload_finance_document(
    loan_id: UUID,
    payload: LoanDocumentCreateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_MANAGE_FINANCE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    await _get_application_or_404(db, ctx, loan_id)
    if payload.document_type not in {
        LoanDocumentType.PAYMENT_INSTRUCTIONS,
        LoanDocumentType.PAYMENT_CONFIRMATION,
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "Finance documents must be PAYMENT_INSTRUCTIONS or PAYMENT_CONFIRMATION",
                "details": {"document_type": payload.document_type},
            },
        )
    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type="FINANCE_PROCESSING",
        document_type=payload.document_type.value,
        file_name=payload.file_name,
        storage_path_or_url=payload.storage_path_or_url,
        uploaded_by_user_id=current_user.id,
    )
    db.add(document)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_document.created",
        resource_type="loan_document",
        resource_id=str(document.id),
        old_value=None,
        new_value=model_snapshot(document),
    )
    await db.commit()
    await db.refresh(document)
    return LoanDocumentDTO.model_validate(document)


@router.post(
    "/{loan_id}/documents/finance/upload",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Finance loan document (file)",
)
async def upload_finance_document_file(
    loan_id: UUID,
    document_type: LoanDocumentType = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_MANAGE_FINANCE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    await _get_application_or_404(db, ctx, loan_id)
    allowed_types = {
        LoanDocumentType.PAYMENT_INSTRUCTIONS,
        LoanDocumentType.PAYMENT_CONFIRMATION,
    }
    if document_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "Finance documents must be Payment Instructions or Payment Confirmation",
                "details": {"document_type": document_type},
            },
        )
    document = await _save_local_document(
        db=db,
        ctx=ctx,
        loan_id=loan_id,
        document_type=document_type,
        stage_type="FINANCE_PROCESSING",
        file=file,
        actor_id=current_user.id,
    )
    return LoanDocumentDTO.model_validate(document)


@router.get(
    "/{loan_id}/legal",
    response_model=LoanLegalReviewResponse,
    summary="Get Legal view for a loan application",
)
async def get_legal_review(
    loan_id: UUID,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_QUEUE_LEGAL_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanLegalReviewResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    legal_stage = None
    for stage in application.workflow_stages or []:
        if stage.stage_type == "LEGAL_EXECUTION":
            legal_stage = stage
            break
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(application)
    applicant = await _fetch_applicant_summary(db, ctx, application)
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
        }
    )
    return LoanLegalReviewResponse(
        loan_application=loan_payload,
        legal_stage=LoanWorkflowStageDTO.model_validate(legal_stage) if legal_stage else None,
    )


@router.patch(
    "/{loan_id}/legal",
    response_model=LoanWorkflowStageDTO,
    summary="Update Legal execution stage",
)
async def update_legal_stage(
    loan_id: UUID,
    payload: LoanWorkflowStageUpdateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_WORKFLOW_LEGAL_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanWorkflowStageDTO:
    stage = await _get_legal_stage_or_404(db, ctx, loan_id)
    stage.loan_application = await _get_application_or_404(db, ctx, loan_id)
    stage.loan_application = await _get_application_or_404(db, ctx, loan_id)
    old_snapshot = model_snapshot(stage)
    if payload.status not in {LoanWorkflowStageStatus.IN_PROGRESS, LoanWorkflowStageStatus.COMPLETED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_stage_status",
                "message": "Legal stage status must be IN_PROGRESS or COMPLETED",
                "details": {"status": payload.status},
            },
        )
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        required_types = {
            LoanDocumentType.STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT.value,
            LoanDocumentType.SECURED_PROMISSORY_NOTE.value,
            LoanDocumentType.STOCK_POWER_AND_ASSIGNMENT.value,
            LoanDocumentType.INVESTMENT_REPRESENTATION_STATEMENT.value,
        }
        doc_stmt = select(LoanDocument.document_type).where(
            LoanDocument.org_id == ctx.org_id,
            LoanDocument.loan_application_id == loan_id,
            LoanDocument.stage_type == "LEGAL_EXECUTION",
            LoanDocument.document_type.in_(required_types),
        )
        doc_result = await db.execute(doc_stmt)
        present = {row[0] for row in doc_result.all()}
        missing = sorted(required_types - present)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "document_required",
                    "message": "All required legal documents must be uploaded before completing Legal execution",
                    "details": {"missing_document_types": missing},
                },
            )

    stage.status = payload.status.value
    stage.notes = payload.notes
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        stage.completed_at = datetime.now(timezone.utc)
        stage.completed_by_user_id = current_user.id
    else:
        stage.completed_at = None
        stage.completed_by_user_id = None

    db.add(stage)
    await loan_workflow.try_activate_loan(db, ctx, stage.loan_application, actor_id=current_user.id)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_workflow_stage.updated",
        resource_type="loan_workflow_stage",
        resource_id=str(stage.id),
        old_value=old_snapshot,
        new_value=model_snapshot(stage),
    )
    await db.commit()
    await db.refresh(stage)
    return LoanWorkflowStageDTO.model_validate(stage)


@router.post(
    "/{loan_id}/documents/legal",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Legal loan document",
)
async def upload_legal_document(
    loan_id: UUID,
    payload: LoanDocumentCreateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_MANAGE_LEGAL)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    await _get_application_or_404(db, ctx, loan_id)
    allowed_types = {
        LoanDocumentType.STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT,
        LoanDocumentType.SECURED_PROMISSORY_NOTE,
        LoanDocumentType.STOCK_POWER_AND_ASSIGNMENT,
        LoanDocumentType.INVESTMENT_REPRESENTATION_STATEMENT,
    }
    if payload.document_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "Legal documents must be execution documents for the loan",
                "details": {"document_type": payload.document_type},
            },
        )
    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type="LEGAL_EXECUTION",
        document_type=payload.document_type.value,
        file_name=payload.file_name,
        storage_path_or_url=payload.storage_path_or_url,
        uploaded_by_user_id=current_user.id,
    )
    db.add(document)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_document.created",
        resource_type="loan_document",
        resource_id=str(document.id),
        old_value=None,
        new_value=model_snapshot(document),
    )
    await db.commit()
    await db.refresh(document)
    return LoanDocumentDTO.model_validate(document)


@router.post(
    "/{loan_id}/documents/legal/upload",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Legal loan document (file)",
)
async def upload_legal_document_file(
    loan_id: UUID,
    document_type: LoanDocumentType = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_MANAGE_LEGAL)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    await _get_application_or_404(db, ctx, loan_id)
    allowed_types = {
        LoanDocumentType.STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT,
        LoanDocumentType.SECURED_PROMISSORY_NOTE,
        LoanDocumentType.STOCK_POWER_AND_ASSIGNMENT,
        LoanDocumentType.INVESTMENT_REPRESENTATION_STATEMENT,
    }
    if document_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "Legal documents must be execution documents for the loan",
                "details": {"document_type": document_type},
            },
        )
    document = await _save_local_document(
        db=db,
        ctx=ctx,
        loan_id=loan_id,
        document_type=document_type,
        stage_type="LEGAL_EXECUTION",
        file=file,
        actor_id=current_user.id,
    )
    return LoanDocumentDTO.model_validate(document)


@router.post(
    "/{loan_id}/documents/legal-issuance",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Legal post-issuance document",
)
async def upload_legal_issuance_document(
    loan_id: UUID,
    payload: LoanDocumentCreateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_WORKFLOW_POST_ISSUANCE_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    application = await _get_application_or_404(db, ctx, loan_id)
    if application.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_status",
                "message": "Loan must be ACTIVE before uploading share certificates",
                "details": {"status": application.status},
            },
        )
    if payload.document_type != LoanDocumentType.SHARE_CERTIFICATE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "Legal post-issuance documents must be SHARE_CERTIFICATE",
                "details": {"document_type": payload.document_type},
            },
        )

    stage_stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == loan_id,
        LoanWorkflowStage.stage_type == "LEGAL_POST_ISSUANCE",
    )
    stage_result = await db.execute(stage_stmt)
    stage = stage_result.scalar_one_or_none()
    if not stage:
        stage = LoanWorkflowStage(
            org_id=ctx.org_id,
            loan_application_id=loan_id,
            stage_type=LoanWorkflowStageType.LEGAL_POST_ISSUANCE.value,
            status="PENDING",
            assigned_role_hint="LEGAL",
        )
        db.add(stage)

    old_stage = model_snapshot(stage)
    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type="LEGAL_POST_ISSUANCE",
        document_type=payload.document_type.value,
        file_name=payload.file_name,
        storage_path_or_url=payload.storage_path_or_url,
        uploaded_by_user_id=current_user.id,
    )
    db.add(document)
    stage.status = "COMPLETED"
    stage.completed_at = datetime.now(timezone.utc)
    stage.completed_by_user_id = current_user.id

    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_workflow_stage.updated",
        resource_type="loan_workflow_stage",
        resource_id=str(stage.id),
        old_value=old_stage,
        new_value=model_snapshot(stage),
    )
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_document.created",
        resource_type="loan_document",
        resource_id=str(document.id),
        old_value=None,
        new_value=model_snapshot(document),
    )
    await db.commit()
    await db.refresh(document)
    return LoanDocumentDTO.model_validate(document)


@router.post(
    "/{loan_id}/documents/legal-issuance/upload",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Legal post-issuance document (file)",
)
async def upload_legal_issuance_document_file(
    loan_id: UUID,
    document_type: LoanDocumentType = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_MANAGE_LEGAL)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    application = await _get_application_or_404(db, ctx, loan_id)
    if application.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_status",
                "message": "Loan must be ACTIVE before uploading share certificates",
                "details": {"status": application.status},
            },
        )
    if document_type != LoanDocumentType.SHARE_CERTIFICATE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "Legal post-issuance documents must be SHARE_CERTIFICATE",
                "details": {"document_type": document_type},
            },
        )
    stage_stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == loan_id,
        LoanWorkflowStage.stage_type == "LEGAL_POST_ISSUANCE",
    )
    stage_result = await db.execute(stage_stmt)
    stage = stage_result.scalar_one_or_none()
    if not stage:
        stage = LoanWorkflowStage(
            org_id=ctx.org_id,
            loan_application_id=loan_id,
            stage_type=LoanWorkflowStageType.LEGAL_POST_ISSUANCE.value,
            status="PENDING",
            assigned_role_hint="LEGAL",
        )
        db.add(stage)

    base_dir = Path(settings.local_upload_dir)
    relative_path, original_name = await save_upload(
        file,
        base_dir=base_dir,
        subdir=Path("loan-documents") / ctx.org_id / str(loan_id),
    )

    old_stage = model_snapshot(stage)
    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type="LEGAL_POST_ISSUANCE",
        document_type=document_type.value,
        file_name=original_name,
        storage_path_or_url=relative_path,
        uploaded_by_user_id=current_user.id,
    )
    db.add(document)
    stage.status = "COMPLETED"
    stage.completed_at = datetime.now(timezone.utc)
    stage.completed_by_user_id = current_user.id

    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_workflow_stage.updated",
        resource_type="loan_workflow_stage",
        resource_id=str(stage.id),
        old_value=old_stage,
        new_value=model_snapshot(stage),
    )
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action="loan_document.created",
        resource_type="loan_document",
        resource_id=str(document.id),
        old_value=None,
        new_value=model_snapshot(document),
    )
    await db.commit()
    await db.refresh(document)
    return LoanDocumentDTO.model_validate(document)
