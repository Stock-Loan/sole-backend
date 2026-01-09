from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import (
    LoanApplicationDTO,
    LoanApplicationListResponse,
    LoanApplicationSummaryDTO,
    LoanDocumentCreateRequest,
    LoanDocumentDTO,
    LoanDocumentType,
    LoanFinanceReviewResponse,
    LoanHRReviewResponse,
    LoanLegalReviewResponse,
    LoanWorkflowStageType,
    LoanWorkflowStageDTO,
    LoanWorkflowStageStatus,
    LoanWorkflowStageUpdateRequest,
)
from app.services import loan_applications, loan_queue, loan_workflow, stock_summary


router = APIRouter(prefix="/org/loans", tags=["loan-admin"])


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
        items=[LoanApplicationSummaryDTO.model_validate(app) for app in applications],
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
        items=[LoanApplicationSummaryDTO.model_validate(app) for app in applications],
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
        items=[LoanApplicationSummaryDTO.model_validate(app) for app in applications],
        total=total,
    )


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
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
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
        doc_stmt = select(LoanDocument).where(
            LoanDocument.org_id == ctx.org_id,
            LoanDocument.loan_application_id == loan_id,
            LoanDocument.stage_type == "HR_REVIEW",
            LoanDocument.document_type == LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT.value,
        )
        doc_result = await db.execute(doc_stmt)
        required_doc = doc_result.scalar_one_or_none()
        if not required_doc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "document_required",
                    "message": "Notice of Stock Option Grant document is required before completing HR review",
                    "details": {"document_type": LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT.value},
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
    if payload.document_type != LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "HR documents must be Notice of Stock Option Grant",
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
    await db.commit()
    await db.refresh(document)
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
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
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
    await db.commit()
    await db.refresh(document)
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
    loan_payload = LoanApplicationDTO.model_validate(application).model_copy(
        update={
            "has_share_certificate": has_share_certificate,
            "has_83b_election": has_83b_election,
            "days_until_83b_due": days_until,
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
            LoanDocumentType.SPOUSE_PARTNER_CONSENT.value,
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
        LoanDocumentType.SPOUSE_PARTNER_CONSENT,
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
    await db.commit()
    await db.refresh(document)
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

    await db.commit()
    await db.refresh(document)
    return LoanDocumentDTO.model_validate(document)
