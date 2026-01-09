from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.loan_application import LoanApplication
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import LoanDocumentCreateRequest, LoanDocumentDTO, LoanDocumentType, LoanWorkflowStageType
from app.services import loan_applications


router = APIRouter(prefix="/me/loans", tags=["loan-borrower"])


async def _get_application_or_404(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application_id: UUID,
    membership_id,
):
    stmt = select(LoanApplication).where(
        LoanApplication.org_id == ctx.org_id,
        LoanApplication.id == application_id,
        LoanApplication.org_membership_id == membership_id,
    )
    result = await db.execute(stmt)
    application = result.scalar_one_or_none()
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan application not found")
    return application


@router.post(
    "/{loan_id}/documents/83b",
    response_model=LoanDocumentDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Upload borrower 83(b) election document",
)
async def upload_83b_document(
    loan_id: UUID,
    payload: LoanDocumentCreateRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_SELF_UPLOAD_83B)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentDTO:
    membership = await loan_applications.get_membership_for_user(db, ctx, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    application = await _get_application_or_404(db, ctx, loan_id, membership.id)
    if application.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_status",
                "message": "Loan must be ACTIVE before uploading 83(b) documents",
                "details": {"status": application.status},
            },
        )
    if payload.document_type != LoanDocumentType.SECTION_83B_ELECTION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_document_type",
                "message": "Borrower documents must be SECTION_83B_ELECTION",
                "details": {"document_type": payload.document_type},
            },
        )

    stage_stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == loan_id,
        LoanWorkflowStage.stage_type == "BORROWER_83B_ELECTION",
    )
    stage_result = await db.execute(stage_stmt)
    stage = stage_result.scalar_one_or_none()
    if not stage:
        stage = LoanWorkflowStage(
            org_id=ctx.org_id,
            loan_application_id=loan_id,
            stage_type=LoanWorkflowStageType.BORROWER_83B_ELECTION.value,
            status="PENDING",
            assigned_role_hint="BORROWER",
        )
        db.add(stage)

    document = LoanDocument(
        org_id=ctx.org_id,
        loan_application_id=loan_id,
        stage_type="BORROWER_83B_ELECTION",
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
