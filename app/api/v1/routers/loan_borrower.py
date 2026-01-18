from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.core.settings import settings
from app.db.session import get_db
from app.models.loan_application import LoanApplication
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import (
    LoanDocumentCreateRequest,
    LoanDocumentDTO,
    LoanDocumentGroup,
    LoanDocumentListResponse,
    LoanDocumentType,
    LoanRepaymentDTO,
    LoanRepaymentListResponse,
    LoanScheduleResponse,
    LoanScheduleWhatIfRequest,
    LoanWorkflowStageType,
)
from app.services import loan_applications, loan_exports, loan_repayments, loan_schedules
from app.services.audit import model_snapshot, record_audit_log
from app.services.local_uploads import resolve_local_path


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


@router.get(
    "/{loan_id}/documents",
    response_model=LoanDocumentListResponse,
    summary="List borrower loan documents",
)
async def list_borrower_documents(
    loan_id: UUID,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_SELF_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanDocumentListResponse:
    membership = await loan_applications.get_membership_for_user(db, ctx, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    await _get_application_or_404(db, ctx, loan_id, membership.id)

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
    "/documents/{document_id}/download",
    response_class=FileResponse,
    summary="Download borrower loan document file",
)
async def download_borrower_document(
    document_id: UUID,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_DOCUMENT_SELF_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    membership = await loan_applications.get_membership_for_user(db, ctx, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    stmt = (
        select(LoanDocument)
        .join(LoanApplication, LoanApplication.id == LoanDocument.loan_application_id)
        .where(
            LoanDocument.org_id == ctx.org_id,
            LoanDocument.id == document_id,
            LoanApplication.org_membership_id == membership.id,
        )
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
    "/{loan_id}/repayments",
    response_model=LoanRepaymentListResponse,
    summary="List borrower loan repayments",
)
async def list_borrower_repayments(
    loan_id: UUID,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_PAYMENT_SELF_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanRepaymentListResponse:
    membership = await loan_applications.get_membership_for_user(db, ctx, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    await _get_application_or_404(db, ctx, loan_id, membership.id)
    repayments = await loan_repayments.list_repayments(db, ctx, loan_id)
    return LoanRepaymentListResponse(
        loan_id=loan_id,
        total=len(repayments),
        items=[LoanRepaymentDTO.model_validate(item) for item in repayments],
    )


@router.get(
    "/{loan_id}/schedule",
    response_model=LoanScheduleResponse,
    summary="Get borrower loan amortization schedule",
)
async def get_borrower_schedule(
    loan_id: UUID,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_SCHEDULE_SELF_VIEW)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanScheduleResponse:
    membership = await loan_applications.get_membership_for_user(db, ctx, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    application = await _get_application_or_404(db, ctx, loan_id, membership.id)
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
    summary="Run borrower loan schedule what-if simulation",
)
async def get_borrower_schedule_what_if(
    loan_id: UUID,
    payload: LoanScheduleWhatIfRequest,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_WHAT_IF_SELF_SIMULATE)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> LoanScheduleResponse:
    membership = await loan_applications.get_membership_for_user(db, ctx, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    application = await _get_application_or_404(db, ctx, loan_id, membership.id)
    try:
        return loan_schedules.build_schedule_what_if(application, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_schedule", "message": str(exc), "details": {}},
        ) from exc


@router.get(
    "/{loan_id}/export",
    response_class=StreamingResponse,
    summary="Export borrower loan details as CSV",
)
async def export_borrower_loan(
    loan_id: UUID,
    current_user=Depends(deps.require_permission(PermissionCode.LOAN_EXPORT_SELF)),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    membership = await loan_applications.get_membership_for_user(db, ctx, current_user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    application = await _get_application_or_404(db, ctx, loan_id, membership.id)
    try:
        schedule = loan_schedules.build_schedule(application)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_schedule", "message": str(exc), "details": {}},
        ) from exc
    content = loan_exports.loan_export_to_csv(application, schedule)
    filename = f"loan_export_{loan_id}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    old_stage = model_snapshot(stage)
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
