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
    LoanHRReviewResponse,
    LoanWorkflowStageDTO,
    LoanWorkflowStageStatus,
    LoanWorkflowStageUpdateRequest,
)
from app.services import loan_applications, loan_queue, stock_summary


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
    return LoanHRReviewResponse(
        loan_application=LoanApplicationDTO.model_validate(application),
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
