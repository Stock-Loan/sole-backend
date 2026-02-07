from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
    Request,
)
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.core.settings import settings
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.schemas.loan import (
    LoanAdminUpdateRequest,
    LoanAdminEditRequest,
    LoanApplicationDTO,
    LoanApplicationListResponse,
    LoanApplicationSummaryDTO,
    LoanActivationMaintenanceResponse,
    LoanApplicantSummaryDTO,
    LoanStageAssigneeSummaryDTO,
    LoanApplicationStatus,
    LoanDocumentCreateRequest,
    LoanDocumentUploadUrlRequest,
    LoanDocumentUploadUrlResponse,
    LoanDocumentGroup,
    LoanDocumentListResponse,
    LoanDocumentDTO,
    LoanDocumentType,
    LoanRepaymentCreateRequest,
    LoanRepaymentEvidenceUploadUrlRequest,
    LoanRepaymentEvidenceUploadUrlResponse,
    LoanRepaymentDTO,
    LoanRepaymentListResponse,
    LoanRepaymentRecordResponse,
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
from app.schemas.settings import MfaEnforcementAction
from app.services import (
    authz,
    loan_applications,
    loan_exports,
    loan_payment_status,
    loan_quotes,
    loan_queue,
    loan_repayments,
    loan_schedules,
    loan_workflow,
    stock_summary,
)
from app.services.audit import model_snapshot, record_audit_log
from app.services.local_uploads import (
    ensure_org_scoped_key,
    generate_storage_key,
    loan_documents_subdir,
    loan_repayments_subdir,
    resolve_local_path,
    save_upload,
)
from app.services.storage.service import get_storage_adapter


router = APIRouter(prefix="/org/loans", tags=["loan-admin"])

ALLOWED_REPAYMENT_EVIDENCE_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

SAFE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}

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
    try:
        relative_path, original_name = await save_upload(
            file,
            base_dir=base_dir,
            subdir=loan_documents_subdir(ctx.org_id, loan_id),
            allowed_extensions=SAFE_EXTENSIONS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type=stage_type,
        document_type=document_type.value,
        file_name=original_name,
        storage_path_or_url=relative_path,
        storage_provider="local",
        storage_bucket=None,
        storage_object_key=relative_path,
        content_type=file.content_type,
        size_bytes=getattr(file, "size", None),
        checksum=None,
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


def _normalize_storage_key(payload: LoanDocumentCreateRequest) -> str:
    if payload.storage_key:
        return payload.storage_key
    if payload.storage_path_or_url:
        return payload.storage_path_or_url
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "missing_storage_key",
            "message": "storage_key is required for non-file uploads",
            "details": {},
        },
    )


async def _create_document_from_storage(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id: UUID,
    document_type: LoanDocumentType,
    stage_type: str,
    payload: LoanDocumentCreateRequest,
    actor_id: UUID,
) -> LoanDocument:
    storage_key = _normalize_storage_key(payload)
    if storage_key.startswith("http"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_storage_key",
                "message": "storage_key must be an object key, not a URL",
                "details": {},
            },
        )
    try:
        ensure_org_scoped_key(ctx.org_id, storage_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_storage_key",
                "message": "storage_key is not scoped to org",
                "details": {},
            },
        ) from exc
    storage_provider = payload.storage_provider or settings.storage_provider
    storage_bucket = payload.storage_bucket or settings.gcs_bucket
    if storage_provider == "gcs" and not storage_bucket:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_storage_bucket",
                "message": "storage_bucket is required for GCS uploads",
                "details": {},
            },
        )
    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type=stage_type,
        document_type=document_type.value,
        file_name=payload.file_name,
        storage_path_or_url=storage_key,
        storage_provider=storage_provider,
        storage_bucket=storage_bucket,
        storage_object_key=storage_key,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        checksum=payload.checksum,
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


def _stage_for_document_type(doc_type: LoanDocumentType) -> LoanWorkflowStageType:
    if doc_type in {
        LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT,
        LoanDocumentType.SPOUSE_PARTNER_CONSENT,
    }:
        return LoanWorkflowStageType.HR_REVIEW
    if doc_type in {
        LoanDocumentType.PAYMENT_INSTRUCTIONS,
        LoanDocumentType.PAYMENT_CONFIRMATION,
    }:
        return LoanWorkflowStageType.FINANCE_PROCESSING
    if doc_type in {
        LoanDocumentType.STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT,
        LoanDocumentType.SECURED_PROMISSORY_NOTE,
        LoanDocumentType.STOCK_POWER_AND_ASSIGNMENT,
        LoanDocumentType.INVESTMENT_REPRESENTATION_STATEMENT,
    }:
        return LoanWorkflowStageType.LEGAL_EXECUTION
    if doc_type == LoanDocumentType.SHARE_CERTIFICATE:
        return LoanWorkflowStageType.LEGAL_POST_ISSUANCE
    if doc_type == LoanDocumentType.SECTION_83B_ELECTION:
        return LoanWorkflowStageType.BORROWER_83B_ELECTION
    raise ValueError(f"Unsupported document type: {doc_type}")


def _document_manage_permission(stage_type: LoanWorkflowStageType) -> PermissionCode:
    if stage_type == LoanWorkflowStageType.HR_REVIEW:
        return PermissionCode.LOAN_DOCUMENT_MANAGE_HR
    if stage_type == LoanWorkflowStageType.FINANCE_PROCESSING:
        return PermissionCode.LOAN_DOCUMENT_MANAGE_FINANCE
    if stage_type in {
        LoanWorkflowStageType.LEGAL_EXECUTION,
        LoanWorkflowStageType.LEGAL_POST_ISSUANCE,
    }:
        return PermissionCode.LOAN_DOCUMENT_MANAGE_LEGAL
    raise ValueError(f"Unsupported stage type: {stage_type}")


def _build_applicant_summary(
    membership, user, department, profile: OrgUserProfile | None
) -> LoanApplicantSummaryDTO:
    full_name = profile.full_name if profile and profile.full_name else user.email
    return LoanApplicantSummaryDTO(
        org_membership_id=membership.id,
        user_id=user.id,
        full_name=full_name,
        email=user.email,
        employee_id=membership.employee_id,
        department_id=membership.department_id,
        department_name=department.name if department else None,
    )


def _build_admin_summary(row) -> LoanApplicationSummaryDTO:
    (
        application,
        membership,
        user,
        department,
        stage_type,
        stage_status,
        assigned_user,
        assigned_at,
        applicant_profile,
        assigned_profile,
    ) = row
    applicant = _build_applicant_summary(membership, user, department, applicant_profile)
    assignee = None
    if assigned_user is not None:
        assignee_name = (
            assigned_profile.full_name
            if assigned_profile and assigned_profile.full_name
            else assigned_user.email
        )
        assignee = LoanStageAssigneeSummaryDTO(
            user_id=assigned_user.id,
            full_name=assignee_name,
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


def _current_stage_from_workflow(stages: list[LoanWorkflowStage] | None):
    if not stages:
        return None, None, None, None
    ordered = sorted(
        stages,
        key=lambda stage: stage.created_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    for stage in ordered:
        if str(stage.status) != LoanWorkflowStageStatus.COMPLETED.value:
            assignee = None
            if getattr(stage, "assigned_to_user", None) is not None:
                assigned_user = stage.assigned_to_user
                assigned_profile = getattr(assigned_user, "profile", None)
                assignee_name = (
                    assigned_profile.full_name
                    if assigned_profile and assigned_profile.full_name
                    else assigned_user.email
                )
                assignee = LoanStageAssigneeSummaryDTO(
                    user_id=assigned_user.id,
                    full_name=assignee_name,
                    email=assigned_user.email,
                )
            return stage.stage_type, stage.status, assignee, stage.assigned_at
    return None, None, None, None


async def _fetch_applicant_summary(
    db: AsyncSession, ctx: deps.TenantContext, application
) -> LoanApplicantSummaryDTO | None:
    membership_bundle = await loan_applications.get_membership_with_user(
        db, ctx, application.org_membership_id
    )
    if not membership_bundle:
        return None
    membership, user, department, profile = membership_bundle
    return _build_applicant_summary(membership, user, department, profile)


async def _fetch_last_edit_note(
    db: AsyncSession, ctx: deps.TenantContext, loan_id: UUID
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
            AuditLog.resource_id == str(loan_id),
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


async def _payment_status_fields(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application,
    *,
    as_of_date: date | None = None,
) -> dict:
    if as_of_date is None:
        as_of_date = date.today()
    try:
        repayments_for_status = await loan_repayments.list_repayments_up_to(
            db,
            ctx,
            application.id,
            as_of_date=as_of_date,
        )
        status_snapshot = loan_payment_status.compute_payment_status(
            application,
            repayments_for_status,
            as_of_date,
        )
    except ValueError:
        return {}
    return {
        "next_payment_date": status_snapshot.next_payment_date,
        "next_payment_amount": status_snapshot.next_payment_amount,
        "next_principal_due": status_snapshot.next_principal_due,
        "next_interest_due": status_snapshot.next_interest_due,
        "principal_remaining": status_snapshot.principal_remaining,
        "interest_remaining": status_snapshot.interest_remaining,
        "total_remaining": status_snapshot.total_remaining,
        "missed_payment_count": status_snapshot.missed_payment_count,
        "missed_payment_amount_total": status_snapshot.missed_payment_amount_total,
        "missed_payment_dates": status_snapshot.missed_payment_dates,
    }


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
                "details": {
                    "created_from": created_from.isoformat(),
                    "created_to": created_to.isoformat(),
                },
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
    (
        checked,
        activated,
        activated_ids,
        post_issuance_completed_ids,
    ) = await loan_workflow.activate_backlog(
        db,
        ctx,
        loan_id=str(loan_id) if loan_id else None,
        limit=limit,
        offset=offset,
        actor_id=current_user.id,
    )
    if loan_id and checked == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    activated = await loan_workflow.try_activate_loan(db, ctx, application)
    if activated:
        await db.commit()
        refreshed = await loan_applications.get_application_with_related(db, ctx, loan_id)
        if not refreshed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
            )
        application = refreshed
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(
        application
    )
    (
        current_stage_type,
        current_stage_status,
        current_stage_assignee,
        current_stage_assigned_at,
    ) = _current_stage_from_workflow(application.workflow_stages or [])
    applicant = await _fetch_applicant_summary(db, ctx, application)
    last_edit_note, last_edited_at, last_edited_by = await _fetch_last_edit_note(db, ctx, loan_id)
    payment_fields = await _payment_status_fields(db, ctx, application)
    return LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
            "current_stage_type": current_stage_type,
            "current_stage_status": current_stage_status,
            "current_stage_assignee": current_stage_assignee,
            "current_stage_assigned_at": current_stage_assigned_at,
            "last_edit_note": last_edit_note,
            "last_edited_at": last_edited_at,
            "last_edited_by": last_edited_by,
            **payment_fields,
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
    stmt = (
        select(LoanDocument)
        .options(selectinload(LoanDocument.uploaded_by_user))
        .where(
            LoanDocument.org_id == ctx.org_id,
            LoanDocument.loan_application_id == loan_id,
        )
        .order_by(LoanDocument.uploaded_at.desc())
    )
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


@router.post(
    "/{loan_id}/documents/upload-url",
    response_model=LoanDocumentUploadUrlResponse,
    summary="Create a signed upload URL for a loan document",
)
async def create_loan_document_upload_url(
    loan_id: UUID,
    payload: LoanDocumentUploadUrlRequest,
    current_user=Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentUploadUrlResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    try:
        stage_type = _stage_for_document_type(payload.document_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": str(exc),
                "details": {"document_type": payload.document_type},
            },
        ) from exc
    if stage_type == LoanWorkflowStageType.BORROWER_83B_ELECTION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "Borrower documents must be uploaded via self-service endpoint",
                "details": {"document_type": payload.document_type},
            },
        )
    if stage_type == LoanWorkflowStageType.LEGAL_POST_ISSUANCE and application.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_status",
                "message": "Loan must be ACTIVE before uploading share certificates",
                "details": {"status": application.status},
            },
        )
    try:
        required_permission = _document_manage_permission(stage_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_stage_type", "message": str(exc), "details": {}},
        ) from exc
    allowed = await authz.check_permission(current_user, ctx, required_permission, db)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {required_permission.value}",
        )

    ext = Path(payload.file_name).suffix.lower()
    if ext and ext not in SAFE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_file_type",
                "message": "File type not allowed",
                "details": {"extension": ext},
            },
        )
    storage_key, original_name = generate_storage_key(
        loan_documents_subdir(ctx.org_id, loan_id), payload.file_name
    )
    try:
        adapter = get_storage_adapter()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "storage_not_configured", "message": str(exc), "details": {}},
        ) from exc
    upload_info = adapter.generate_upload_url(storage_key, payload.content_type, payload.size_bytes)
    return LoanDocumentUploadUrlResponse(
        upload_url=upload_info["upload_url"],
        required_headers_or_fields=upload_info.get("headers", {}),
        storage_provider=adapter.provider,
        storage_bucket=adapter.bucket,
        storage_key=storage_key,
        file_name=original_name,
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
    response_model=LoanRepaymentRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a loan repayment with optional evidence",
)
async def record_loan_repayment_with_evidence(
    loan_id: UUID,
    amount: str | None = Form(default=None),
    principal_amount: str | None = Form(default=None),
    interest_amount: str | None = Form(default=None),
    extra_principal_amount: str | None = Form(default=None),
    extra_interest_amount: str | None = Form(default=None),
    payment_date: date = Form(...),
    evidence_file: UploadFile | None = File(default=None),
    evidence_file_name: str | None = Form(default=None),
    evidence_storage_key: str | None = Form(default=None),
    evidence_storage_provider: str | None = Form(default=None),
    evidence_storage_bucket: str | None = Form(default=None),
    evidence_size_bytes: int | None = Form(default=None),
    evidence_checksum: str | None = Form(default=None),
    evidence_content_type: str | None = Form(default=None),
    current_user=Depends(
        deps.require_permission_with_mfa(
            PermissionCode.LOAN_PAYMENT_RECORD,
            action=MfaEnforcementAction.LOAN_PAYMENT_RECORD.value,
        )
    ),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanRepaymentRecordResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    if evidence_file and evidence_storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_evidence_input",
                "message": "Provide either evidence_file or evidence_storage_key, not both",
                "details": {},
            },
        )
    extra_principal = Decimal(extra_principal_amount or "0")
    extra_interest = Decimal(extra_interest_amount or "0")
    existing_repayments = await loan_repayments.list_repayments_up_to(
        db,
        ctx,
        loan_id,
        as_of_date=payment_date,
    )
    status_snapshot = loan_payment_status.compute_payment_status(
        application,
        existing_repayments,
        payment_date,
    )
    if status_snapshot.next_payment_date is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "loan_fully_paid",
                "message": "Loan has no scheduled payments remaining",
                "details": {},
            },
        )
    scheduled_principal = status_snapshot.next_principal_due or Decimal("0")
    scheduled_interest = status_snapshot.next_interest_due or Decimal("0")
    principal_total = scheduled_principal + extra_principal
    interest_total = scheduled_interest + extra_interest
    amount_total = principal_total + interest_total
    if (
        status_snapshot.principal_remaining is not None
        and principal_total > status_snapshot.principal_remaining
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "principal_overpayment",
                "message": "principal_amount exceeds remaining principal balance",
                "details": {"remaining_principal": str(status_snapshot.principal_remaining)},
            },
        )
    if (
        status_snapshot.interest_remaining is not None
        and interest_total > status_snapshot.interest_remaining
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "interest_overpayment",
                "message": "interest_amount exceeds remaining interest balance",
                "details": {"remaining_interest": str(status_snapshot.interest_remaining)},
            },
        )
    if amount is not None and Decimal(amount) != amount_total:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_repayment_amount",
                "message": "amount must match scheduled payment plus extras",
                "details": {"expected": str(amount_total)},
            },
        )
    if principal_amount is not None and Decimal(principal_amount) != principal_total:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_principal_amount",
                "message": "principal_amount must match scheduled principal plus extra_principal_amount",
                "details": {"expected": str(principal_total)},
            },
        )
    if interest_amount is not None and Decimal(interest_amount) != interest_total:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_interest_amount",
                "message": "interest_amount must match scheduled interest plus extra_interest_amount",
                "details": {"expected": str(interest_total)},
            },
        )
    payload = LoanRepaymentCreateRequest(
        amount=amount_total,
        principal_amount=principal_total,
        interest_amount=interest_total,
        extra_principal_amount=extra_principal,
        extra_interest_amount=extra_interest,
        payment_date=payment_date,
    )

    evidence_name = evidence_file_name
    evidence_storage_path = None
    evidence_provider = None
    evidence_bucket = None
    evidence_object_key = None
    evidence_content_type_value = None
    evidence_size_value = None
    evidence_checksum_value = None
    if evidence_file:
        if evidence_file.content_type not in ALLOWED_REPAYMENT_EVIDENCE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_evidence_type",
                    "message": "Evidence must be a PDF or image",
                    "details": {"content_type": evidence_file.content_type},
                },
            )
        try:
            relative_path, original_name = await save_upload(
                evidence_file,
                base_dir=Path(settings.local_upload_dir),
                subdir=loan_repayments_subdir(ctx.org_id, loan_id),
                allowed_extensions=SAFE_EXTENSIONS,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        evidence_name = original_name
        evidence_storage_path = relative_path
        evidence_provider = "local"
        evidence_bucket = None
        evidence_object_key = relative_path
        evidence_content_type_value = evidence_file.content_type
        evidence_size_value = getattr(evidence_file, "size", None)
        evidence_checksum_value = None
    elif evidence_storage_key:
        if evidence_storage_key.startswith("http"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_storage_key",
                    "message": "evidence_storage_key must be an object key, not a URL",
                    "details": {},
                },
            )
        try:
            ensure_org_scoped_key(ctx.org_id, evidence_storage_key)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_storage_key",
                    "message": "evidence_storage_key is not scoped to org",
                    "details": {},
                },
            ) from exc
        if not evidence_content_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "missing_evidence_content_type",
                    "message": "evidence_content_type is required for external uploads",
                    "details": {},
                },
            )
        if evidence_content_type not in ALLOWED_REPAYMENT_EVIDENCE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_evidence_type",
                    "message": "Evidence must be a PDF or image",
                    "details": {"content_type": evidence_content_type},
                },
            )
        evidence_name = evidence_name or Path(evidence_storage_key).name
        evidence_storage_path = evidence_storage_key
        evidence_provider = evidence_storage_provider or settings.storage_provider
        evidence_bucket = evidence_storage_bucket or settings.gcs_bucket
        if evidence_provider == "gcs" and not evidence_bucket:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "missing_storage_bucket",
                    "message": "evidence_storage_bucket is required for GCS uploads",
                    "details": {},
                },
            )
        evidence_object_key = evidence_storage_key
        evidence_content_type_value = evidence_content_type
        evidence_size_value = evidence_size_bytes
        evidence_checksum_value = evidence_checksum

    try:
        repayment = await loan_repayments.record_repayment(
            db,
            ctx,
            application,
            payload,
            actor_id=current_user.id,
            evidence_file_name=evidence_name,
            evidence_storage_path_or_url=evidence_storage_path,
            evidence_storage_provider=evidence_provider,
            evidence_storage_bucket=evidence_bucket,
            evidence_storage_object_key=evidence_object_key,
            evidence_content_type=evidence_content_type_value,
            evidence_size_bytes=evidence_size_value,
            evidence_checksum=evidence_checksum_value,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_repayment", "message": str(exc), "details": {}},
        ) from exc
    await db.commit()
    await db.refresh(repayment)

    updated_repayments = existing_repayments + [repayment]
    updated_status = loan_payment_status.compute_payment_status(
        application,
        updated_repayments,
        payment_date,
    )
    return LoanRepaymentRecordResponse(
        repayment=LoanRepaymentDTO.model_validate(repayment),
        next_payment_date=updated_status.next_payment_date,
        next_payment_amount=updated_status.next_payment_amount,
        next_principal_due=updated_status.next_principal_due,
        next_interest_due=updated_status.next_interest_due,
        principal_remaining=updated_status.principal_remaining,
        interest_remaining=updated_status.interest_remaining,
        total_remaining=updated_status.total_remaining,
    )


@router.post(
    "/{loan_id}/repayments/upload-url",
    response_model=LoanRepaymentEvidenceUploadUrlResponse,
    summary="Create a signed upload URL for repayment evidence",
)
async def create_repayment_evidence_upload_url(
    loan_id: UUID,
    payload: LoanRepaymentEvidenceUploadUrlRequest,
    current_user=Depends(
        deps.require_permission_with_mfa(
            PermissionCode.LOAN_PAYMENT_RECORD,
            action=MfaEnforcementAction.LOAN_PAYMENT_RECORD.value,
        )
    ),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanRepaymentEvidenceUploadUrlResponse:
    await _get_application_or_404(db, ctx, loan_id)
    if payload.content_type not in ALLOWED_REPAYMENT_EVIDENCE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_evidence_type",
                "message": "Evidence must be a PDF or image",
                "details": {"content_type": payload.content_type},
            },
        )
    ext = Path(payload.file_name).suffix.lower()
    if ext and ext not in SAFE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_file_type",
                "message": "File type not allowed",
                "details": {"extension": ext},
            },
        )
    storage_key, original_name = generate_storage_key(
        loan_repayments_subdir(ctx.org_id, loan_id), payload.file_name
    )
    try:
        adapter = get_storage_adapter()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "storage_not_configured", "message": str(exc), "details": {}},
        ) from exc
    upload_info = adapter.generate_upload_url(storage_key, payload.content_type, payload.size_bytes)
    return LoanRepaymentEvidenceUploadUrlResponse(
        upload_url=upload_info["upload_url"],
        required_headers_or_fields=upload_info.get("headers", {}),
        storage_provider=adapter.provider,
        storage_bucket=adapter.bucket,
        storage_key=storage_key,
        file_name=original_name,
    )


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
    if document.storage_provider == "gcs":
        adapter = get_storage_adapter(bucket_override=document.storage_bucket)
        download_url = adapter.generate_download_url(
            document.storage_object_key or document.storage_path_or_url,
            expires_in=settings.gcs_signed_url_expiry_seconds,
        )
        return RedirectResponse(download_url)
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
        file_path = resolve_local_path(
            Path(settings.local_upload_dir), document.storage_path_or_url
        )
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
    return FileResponse(
        file_path, filename=document.file_name, media_type="application/octet-stream"
    )


@router.get(
    "/{loan_id}/schedule",
    response_model=LoanScheduleResponse,
    summary="Get loan amortization schedule",
)
async def get_loan_schedule(
    loan_id: UUID,
    as_of: date | None = Query(default=None, description="As-of date for remaining schedule"),
    include_paid: bool = Query(default=False, description="Include fully paid schedule entries"),
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_SCHEDULE_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanScheduleResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    try:
        as_of_date = as_of or date.today()
        repayments = await loan_repayments.list_repayments_up_to(
            db,
            ctx,
            loan_id,
            as_of_date=as_of_date,
        )
        return loan_schedules.build_schedule_remaining(
            application,
            repayments,
            as_of_date=as_of_date,
            include_paid=include_paid,
        )
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
        as_of_date = payload.as_of_date or date.today()
        repayments = await loan_repayments.list_repayments_up_to(
            db,
            ctx,
            loan_id,
            as_of_date=as_of_date,
        )
        return loan_schedules.build_schedule_what_if(application, payload, repayments=repayments)
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
    as_of: date | None = Query(
        default=None, description="As-of date for remaining schedule export"
    ),
    include_paid: bool = Query(default=False, description="Include fully paid schedule entries"),
    _: object = Depends(deps.require_permission(PermissionCode.LOAN_EXPORT_SCHEDULE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    application = await _get_application_or_404(db, ctx, loan_id)
    try:
        as_of_date = as_of or date.today()
        repayments = await loan_repayments.list_repayments_up_to(
            db,
            ctx,
            loan_id,
            as_of_date=as_of_date,
        )
        schedule = loan_schedules.build_schedule_remaining(
            application,
            repayments,
            as_of_date=as_of_date,
            include_paid=include_paid,
        )
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
    if (
        payload.status == LoanApplicationStatus.REJECTED
        and not (payload.decision_reason or "").strip()
    ):
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )

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

    refreshed = await loan_applications.get_application_with_related(db, ctx, updated.id)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )

    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(
        refreshed
    )
    applicant = await _fetch_applicant_summary(db, ctx, refreshed)
    payment_fields = await _payment_status_fields(db, ctx, refreshed)
    return LoanApplicationDTO.model_validate(refreshed).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
            **payment_fields,
        }
    )


@router.patch(
    "/{loan_id}/edit",
    response_model=LoanApplicationDTO,
    summary="Edit loan application values (admin)",
)
async def edit_loan_application(
    loan_id: UUID,
    payload: LoanAdminEditRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanApplicationDTO:
    if not payload.note or not payload.note.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "edit_note_required",
                "message": "note is required for loan edits",
                "details": {"field": "note"},
            },
        )

    application = await loan_applications.get_application_with_related(db, ctx, loan_id)
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )

    try:
        updated = await loan_applications.update_admin_application_fields(
            db,
            ctx,
            application,
            payload,
            actor_id=current_user.id,
        )
    except loan_quotes.LoanQuoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_edit", "message": str(exc), "details": {}},
        ) from exc

    refreshed = await loan_applications.get_application_with_related(db, ctx, updated.id)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(
        refreshed
    )
    (
        current_stage_type,
        current_stage_status,
        current_stage_assignee,
        current_stage_assigned_at,
    ) = _current_stage_from_workflow(refreshed.workflow_stages or [])
    applicant = await _fetch_applicant_summary(db, ctx, refreshed)
    last_edit_note, last_edited_at, last_edited_by = await _fetch_last_edit_note(
        db, ctx, refreshed.id
    )
    payment_fields = await _payment_status_fields(db, ctx, refreshed)
    return LoanApplicationDTO.model_validate(refreshed).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
            "current_stage_type": current_stage_type,
            "current_stage_status": current_stage_status,
            "current_stage_assignee": current_stage_assignee,
            "current_stage_assigned_at": current_stage_assigned_at,
            "last_edit_note": last_edit_note,
            "last_edited_at": last_edited_at,
            "last_edited_by": last_edited_by,
            **payment_fields,
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

    user_stmt = select(User).where(User.id == assignee_id)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Assignee membership not found"
        )

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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow stage not found"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="HR workflow stage not found"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Finance workflow stage not found"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Legal workflow stage not found"
        )
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
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(
        application
    )
    applicant = await _fetch_applicant_summary(db, ctx, application)
    last_edit_note, last_edited_at, last_edited_by = await _fetch_last_edit_note(db, ctx, loan_id)
    payment_fields = await _payment_status_fields(db, ctx, application)
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
            "last_edit_note": last_edit_note,
            "last_edited_at": last_edited_at,
            "last_edited_by": last_edited_by,
            **payment_fields,
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
    request: Request,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_WORKFLOW_HR_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanWorkflowStageDTO:
    stage = await _get_hr_stage_or_404(db, ctx, loan_id)
    stage.loan_application = await _get_application_or_404(db, ctx, loan_id)
    old_snapshot = model_snapshot(stage)
    if payload.status not in {
        LoanWorkflowStageStatus.IN_PROGRESS,
        LoanWorkflowStageStatus.COMPLETED,
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_stage_status",
                "message": "HR stage status must be IN_PROGRESS or COMPLETED",
                "details": {"status": payload.status},
            },
        )
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        await deps.require_mfa_for_action(
            request,
            current_user,
            ctx,
            db,
            action=MfaEnforcementAction.WORKFLOW_COMPLETE.value,
        )
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
    document = await _create_document_from_storage(
        db=db,
        ctx=ctx,
        loan_id=loan_id,
        document_type=payload.document_type,
        stage_type="HR_REVIEW",
        payload=payload,
        actor_id=current_user.id,
    )
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
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(
        application
    )
    applicant = await _fetch_applicant_summary(db, ctx, application)
    last_edit_note, last_edited_at, last_edited_by = await _fetch_last_edit_note(db, ctx, loan_id)
    payment_fields = await _payment_status_fields(db, ctx, application)
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
            "last_edit_note": last_edit_note,
            "last_edited_at": last_edited_at,
            "last_edited_by": last_edited_by,
            **payment_fields,
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
    request: Request,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_WORKFLOW_FINANCE_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanWorkflowStageDTO:
    stage = await _get_finance_stage_or_404(db, ctx, loan_id)
    stage.loan_application = await _get_application_or_404(db, ctx, loan_id)
    old_snapshot = model_snapshot(stage)
    if payload.status not in {
        LoanWorkflowStageStatus.IN_PROGRESS,
        LoanWorkflowStageStatus.COMPLETED,
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_stage_status",
                "message": "Finance stage status must be IN_PROGRESS or COMPLETED",
                "details": {"status": payload.status},
            },
        )
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        await deps.require_mfa_for_action(
            request,
            current_user,
            ctx,
            db,
            action=MfaEnforcementAction.WORKFLOW_COMPLETE.value,
        )
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
    document = await _create_document_from_storage(
        db=db,
        ctx=ctx,
        loan_id=loan_id,
        document_type=payload.document_type,
        stage_type="FINANCE_PROCESSING",
        payload=payload,
        actor_id=current_user.id,
    )
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
    has_share_certificate, has_83b_election, days_until = loan_applications._compute_workflow_flags(
        application
    )
    applicant = await _fetch_applicant_summary(db, ctx, application)
    last_edit_note, last_edited_at, last_edited_by = await _fetch_last_edit_note(db, ctx, loan_id)
    payment_fields = await _payment_status_fields(db, ctx, application)
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
            "applicant": applicant,
            "last_edit_note": last_edit_note,
            "last_edited_at": last_edited_at,
            "last_edited_by": last_edited_by,
            **payment_fields,
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
    request: Request,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_WORKFLOW_LEGAL_MANAGE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanWorkflowStageDTO:
    stage = await _get_legal_stage_or_404(db, ctx, loan_id)
    stage.loan_application = await _get_application_or_404(db, ctx, loan_id)
    stage.loan_application = await _get_application_or_404(db, ctx, loan_id)
    old_snapshot = model_snapshot(stage)
    if payload.status not in {
        LoanWorkflowStageStatus.IN_PROGRESS,
        LoanWorkflowStageStatus.COMPLETED,
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_stage_status",
                "message": "Legal stage status must be IN_PROGRESS or COMPLETED",
                "details": {"status": payload.status},
            },
        )
    if payload.status == LoanWorkflowStageStatus.COMPLETED:
        await deps.require_mfa_for_action(
            request,
            current_user,
            ctx,
            db,
            action=MfaEnforcementAction.WORKFLOW_COMPLETE.value,
        )
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
    document = await _create_document_from_storage(
        db=db,
        ctx=ctx,
        loan_id=loan_id,
        document_type=payload.document_type,
        stage_type="LEGAL_EXECUTION",
        payload=payload,
        actor_id=current_user.id,
    )
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
    request: Request,
    current_user=Depends(
        deps.require_permission(PermissionCode.LOAN_WORKFLOW_POST_ISSUANCE_MANAGE)
    ),
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
    document = await _create_document_from_storage(
        db=db,
        ctx=ctx,
        loan_id=loan_id,
        document_type=payload.document_type,
        stage_type="LEGAL_POST_ISSUANCE",
        payload=payload,
        actor_id=current_user.id,
    )
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.WORKFLOW_COMPLETE.value,
    )
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
    request: Request,
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
    try:
        relative_path, original_name = await save_upload(
            file,
            base_dir=base_dir,
            subdir=loan_documents_subdir(ctx.org_id, loan_id),
            allowed_extensions=SAFE_EXTENSIONS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

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
    await deps.require_mfa_for_action(
        request,
        current_user,
        ctx,
        db,
        action=MfaEnforcementAction.WORKFLOW_COMPLETE.value,
    )
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
