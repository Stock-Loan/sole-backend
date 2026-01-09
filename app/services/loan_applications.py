from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.audit_log import AuditLog
from app.models.loan_application import LoanApplication
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.models.user import User
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.common import MaritalStatus, normalize_marital_status
from app.schemas.loan import (
    LoanApplicationDraftCreate,
    LoanApplicationDraftUpdate,
    LoanApplicationStatus,
    LoanQuoteRequest,
    LoanSelectionMode,
)
from app.services import loan_quotes, settings as settings_service


def _snapshot_org_settings(settings: OrgSettings) -> dict:
    data: dict[str, object] = {}
    for column in settings.__table__.columns:
        name = column.name
        if name in {"created_at", "updated_at"}:
            continue
        value = getattr(settings, name)
        if isinstance(value, datetime):
            value = value.isoformat()
        if isinstance(value, Decimal):
            value = str(value)
        data[name] = value
    return data


def _serialize_snapshot_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _application_snapshot(application: LoanApplication) -> dict:
    return {
        "id": str(application.id) if application.id else None,
        "org_id": application.org_id,
        "org_membership_id": str(application.org_membership_id),
        "status": application.status,
        "activation_date": _serialize_snapshot_value(application.activation_date),
        "election_83b_due_date": _serialize_snapshot_value(application.election_83b_due_date),
        "version": application.version,
        "as_of_date": _serialize_snapshot_value(application.as_of_date),
        "selection_mode": application.selection_mode,
        "selection_value_snapshot": _serialize_snapshot_value(application.selection_value_snapshot),
        "shares_to_exercise": int(application.shares_to_exercise),
        "total_exercisable_shares_snapshot": int(application.total_exercisable_shares_snapshot),
        "purchase_price": _serialize_snapshot_value(application.purchase_price),
        "down_payment_amount": _serialize_snapshot_value(application.down_payment_amount),
        "loan_principal": _serialize_snapshot_value(application.loan_principal),
        "interest_type": application.interest_type,
        "repayment_method": application.repayment_method,
        "term_months": application.term_months,
        "nominal_annual_rate_percent": _serialize_snapshot_value(application.nominal_annual_rate_percent),
        "estimated_monthly_payment": _serialize_snapshot_value(application.estimated_monthly_payment),
        "total_payable_amount": _serialize_snapshot_value(application.total_payable_amount),
        "total_interest_amount": _serialize_snapshot_value(application.total_interest_amount),
        "quote_inputs_snapshot": application.quote_inputs_snapshot,
        "quote_option_snapshot": application.quote_option_snapshot,
        "allocation_strategy": application.allocation_strategy,
        "allocation_snapshot": application.allocation_snapshot,
        "org_settings_snapshot": application.org_settings_snapshot,
        "eligibility_result_snapshot": application.eligibility_result_snapshot,
        "marital_status_snapshot": application.marital_status_snapshot,
        "spouse_first_name": application.spouse_first_name,
        "spouse_middle_name": application.spouse_middle_name,
        "spouse_last_name": application.spouse_last_name,
        "spouse_email": application.spouse_email,
        "spouse_phone": application.spouse_phone,
        "spouse_address": application.spouse_address,
        "created_at": _serialize_snapshot_value(application.created_at),
        "updated_at": _serialize_snapshot_value(application.updated_at),
    }


def _quote_inputs_snapshot(request: LoanQuoteRequest) -> dict:
    return {
        "selection_mode": request.selection_mode.value
        if isinstance(request.selection_mode, LoanSelectionMode)
        else str(request.selection_mode),
        "selection_value": str(request.selection_value),
        "as_of_date": _serialize_snapshot_value(request.as_of_date),
        "desired_interest_type": _enum_value(request.desired_interest_type),
        "desired_repayment_method": _enum_value(request.desired_repayment_method),
        "desired_term_months": request.desired_term_months,
    }


def _quote_option_snapshot(option) -> dict:
    return option.model_dump(mode="json")


def _allocation_snapshot(allocation) -> list[dict]:
    return [item.model_dump(mode="json") for item in allocation]


def _record_audit_log(
    *,
    db: AsyncSession,
    ctx: deps.TenantContext,
    actor_id,
    action: str,
    application: LoanApplication,
    old_value: dict | None,
) -> None:
    entry = AuditLog(
        org_id=ctx.org_id,
        actor_id=actor_id,
        action=action,
        resource_type="loan_application",
        resource_id=str(application.id),
        old_value=old_value,
        new_value=_application_snapshot(application),
    )
    db.add(entry)


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 100:
        raise loan_quotes.LoanQuoteError(
            code="invalid_idempotency_key",
            message="Idempotency-Key is too long",
            details={"field": "Idempotency-Key", "max_length": 100},
        )
    return cleaned


def _requires_spouse_info(marital_status: str | None) -> bool:
    normalized = normalize_marital_status(marital_status)
    return normalized in {MaritalStatus.MARRIED, MaritalStatus.DOMESTIC_PARTNER}


def _missing_spouse_fields(application: LoanApplication) -> list[str]:
    missing: list[str] = []
    if not (application.spouse_first_name or "").strip():
        missing.append("spouse_first_name")
    if not (application.spouse_last_name or "").strip():
        missing.append("spouse_last_name")
    if not (application.spouse_email or "").strip():
        missing.append("spouse_email")
    if not (application.spouse_phone or "").strip():
        missing.append("spouse_phone")
    if not (application.spouse_address or "").strip():
        missing.append("spouse_address")
    return missing


CORE_WORKFLOW_STAGES: list[tuple[str, str]] = [
    ("HR_REVIEW", "HR"),
    ("FINANCE_PROCESSING", "FINANCE"),
    ("LEGAL_EXECUTION", "LEGAL"),
]


async def _ensure_core_workflow_stages(
    db: AsyncSession, ctx: deps.TenantContext, application: LoanApplication
) -> None:
    if not application.id:
        return
    stmt = select(LoanWorkflowStage).where(
        LoanWorkflowStage.org_id == ctx.org_id,
        LoanWorkflowStage.loan_application_id == application.id,
    )
    result = await db.execute(stmt)
    existing = {stage.stage_type for stage in result.scalars().all()}
    for stage_type, role_hint in CORE_WORKFLOW_STAGES:
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


def _selection_value_from_application(application: LoanApplication) -> Decimal:
    mode = LoanSelectionMode(application.selection_mode)
    if mode == LoanSelectionMode.SHARES:
        return Decimal(int(application.shares_to_exercise))
    total = int(application.total_exercisable_shares_snapshot or 0)
    if total <= 0:
        raise loan_quotes.LoanQuoteError(
            code="invalid_selection",
            message="Cannot derive selection percent from zero exercisable shares",
            details={"total_exercisable_shares_snapshot": total},
        )
    return (Decimal(int(application.shares_to_exercise)) * Decimal("100")) / Decimal(total)


def _apply_quote(
    application: LoanApplication,
    *,
    quote,
    quote_request: LoanQuoteRequest,
    selection_mode: LoanSelectionMode,
    org_settings: OrgSettings,
) -> None:
    option = quote.options[0]
    selection_mode_value = (
        selection_mode.value if isinstance(selection_mode, LoanSelectionMode) else str(selection_mode)
    )
    application.status = LoanApplicationStatus.DRAFT.value
    application.as_of_date = quote.as_of_date
    application.selection_mode = selection_mode_value
    application.selection_value_snapshot = quote.selection_value
    application.shares_to_exercise = quote.shares_to_exercise
    application.total_exercisable_shares_snapshot = quote.total_exercisable_shares
    application.purchase_price = quote.purchase_price
    application.down_payment_amount = quote.down_payment_amount
    application.loan_principal = quote.loan_principal
    application.interest_type = option.interest_type.value
    application.repayment_method = option.repayment_method.value
    application.term_months = option.term_months
    application.nominal_annual_rate_percent = option.nominal_annual_rate
    application.estimated_monthly_payment = option.estimated_monthly_payment
    application.total_payable_amount = option.total_payable
    application.total_interest_amount = option.total_interest
    application.quote_inputs_snapshot = _quote_inputs_snapshot(quote_request)
    application.quote_option_snapshot = _quote_option_snapshot(option)
    application.allocation_strategy = quote.allocation_strategy
    application.allocation_snapshot = _allocation_snapshot(quote.allocation)
    application.org_settings_snapshot = _snapshot_org_settings(org_settings)
    application.eligibility_result_snapshot = quote.eligibility_result.model_dump(mode="json")


async def get_membership_for_user(
    db: AsyncSession, ctx: deps.TenantContext, user_id
) -> OrgMembership | None:
    stmt = select(OrgMembership).where(
        OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == user_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_application_with_related(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application_id,
    *,
    membership_id=None,
) -> LoanApplication | None:
    stmt = (
        select(LoanApplication)
        .options(
            selectinload(LoanApplication.workflow_stages),
            selectinload(LoanApplication.documents),
        )
        .where(
            LoanApplication.org_id == ctx.org_id,
            LoanApplication.id == application_id,
        )
    )
    if membership_id is not None:
        stmt = stmt.where(LoanApplication.org_membership_id == membership_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_draft_application(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership: OrgMembership,
    payload: LoanApplicationDraftCreate,
    *,
    actor_id=None,
    idempotency_key: str | None = None,
) -> LoanApplication:
    idempotency_key = _validate_idempotency_key(idempotency_key)
    if idempotency_key:
        existing_stmt = select(LoanApplication).where(
            LoanApplication.org_id == ctx.org_id,
            LoanApplication.org_membership_id == membership.id,
            LoanApplication.create_idempotency_key == idempotency_key,
        )
        existing_result = await db.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()
        if existing:
            return existing
    quote_request = LoanQuoteRequest(
        selection_mode=payload.selection_mode,
        selection_value=payload.selection_value,
        as_of_date=payload.as_of_date,
        desired_interest_type=payload.desired_interest_type,
        desired_repayment_method=payload.desired_repayment_method,
        desired_term_months=payload.desired_term_months,
    )
    org_settings = await settings_service.get_org_settings(db, ctx)
    quote = await loan_quotes.calculate_loan_quote(db, ctx, membership, quote_request)
    application = LoanApplication(
        org_id=ctx.org_id,
        org_membership_id=membership.id,
        status=LoanApplicationStatus.DRAFT.value,
        create_idempotency_key=idempotency_key,
        marital_status_snapshot=payload.marital_status_snapshot,
        spouse_first_name=payload.spouse_first_name,
        spouse_middle_name=payload.spouse_middle_name,
        spouse_last_name=payload.spouse_last_name,
        spouse_email=payload.spouse_email,
        spouse_phone=payload.spouse_phone,
        spouse_address=payload.spouse_address,
    )
    _apply_quote(
        application,
        quote=quote,
        quote_request=quote_request,
        selection_mode=payload.selection_mode,
        org_settings=org_settings,
    )
    db.add(application)
    await db.flush()
    _record_audit_log(
        db=db,
        ctx=ctx,
        actor_id=actor_id,
        action="loan_application.created",
        application=application,
        old_value=None,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if idempotency_key:
            existing_stmt = select(LoanApplication).where(
                LoanApplication.org_id == ctx.org_id,
                LoanApplication.org_membership_id == membership.id,
                LoanApplication.create_idempotency_key == idempotency_key,
            )
            existing_result = await db.execute(existing_stmt)
            existing = existing_result.scalar_one_or_none()
            if existing:
                return existing
        raise
    await db.refresh(application)
    return application


async def update_draft_application(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership: OrgMembership,
    application: LoanApplication,
    payload: LoanApplicationDraftUpdate,
    *,
    actor_id=None,
) -> LoanApplication:
    if application.status != LoanApplicationStatus.DRAFT.value:
        raise loan_quotes.LoanQuoteError(
            code="invalid_status",
            message="Only DRAFT applications can be updated",
            details={"status": application.status},
        )

    old_snapshot = _application_snapshot(application)

    recalc_fields = {
        "selection_mode",
        "selection_value",
        "as_of_date",
        "desired_interest_type",
        "desired_repayment_method",
        "desired_term_months",
    }
    recalc = any(getattr(payload, field) is not None for field in recalc_fields)
    if recalc:
        selection_mode = payload.selection_mode or LoanSelectionMode(application.selection_mode)
        selection_value = (
            payload.selection_value if payload.selection_value is not None else _selection_value_from_application(application)
        )
        quote_request = LoanQuoteRequest(
            selection_mode=selection_mode,
            selection_value=selection_value,
            as_of_date=payload.as_of_date or application.as_of_date,
            desired_interest_type=payload.desired_interest_type or application.interest_type,
            desired_repayment_method=payload.desired_repayment_method or application.repayment_method,
            desired_term_months=payload.desired_term_months or application.term_months,
        )
        org_settings = await settings_service.get_org_settings(db, ctx)
        quote = await loan_quotes.calculate_loan_quote(db, ctx, membership, quote_request)
        _apply_quote(
            application,
            quote=quote,
            quote_request=quote_request,
            selection_mode=selection_mode,
            org_settings=org_settings,
        )

    for field in [
        "marital_status_snapshot",
        "spouse_first_name",
        "spouse_middle_name",
        "spouse_last_name",
        "spouse_email",
        "spouse_phone",
        "spouse_address",
    ]:
        value = getattr(payload, field)
        if value is not None:
            setattr(application, field, value)

    db.add(application)
    _record_audit_log(
        db=db,
        ctx=ctx,
        actor_id=actor_id,
        action="loan_application.updated",
        application=application,
        old_value=old_snapshot,
    )
    await db.commit()
    await db.refresh(application)
    return application


async def submit_application(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership: OrgMembership,
    application: LoanApplication,
    current_user: User,
    *,
    actor_id=None,
    idempotency_key: str | None = None,
) -> LoanApplication:
    idempotency_key = _validate_idempotency_key(idempotency_key)
    if application.status != LoanApplicationStatus.DRAFT.value:
        if (
            idempotency_key
            and application.status == LoanApplicationStatus.SUBMITTED.value
            and application.submit_idempotency_key == idempotency_key
        ):
            await _ensure_core_workflow_stages(db, ctx, application)
            await db.commit()
            await db.refresh(application)
            return application
        raise loan_quotes.LoanQuoteError(
            code="invalid_status",
            message="Only DRAFT applications can be submitted",
            details={"status": application.status},
        )

    if idempotency_key and application.submit_idempotency_key and application.submit_idempotency_key != idempotency_key:
        raise loan_quotes.LoanQuoteError(
            code="idempotency_conflict",
            message="Submission already processed with a different idempotency key",
            details={"submitted_key": application.submit_idempotency_key},
        )

    old_snapshot = _application_snapshot(application)

    current_status = normalize_marital_status(current_user.marital_status)
    submitted_status = normalize_marital_status(application.marital_status_snapshot)
    if current_status and submitted_status and current_status != submitted_status:
        raise loan_quotes.LoanQuoteError(
            code="marital_status_mismatch",
            message=(
                "Marital status does not match our records. "
                "Please contact HR to update your marital status before submitting."
            ),
            details={
                "current_status": current_user.marital_status,
                "submitted_status": application.marital_status_snapshot,
            },
        )
    if current_status and not submitted_status:
        application.marital_status_snapshot = current_user.marital_status
        submitted_status = current_status

    if _requires_spouse_info(application.marital_status_snapshot):
        missing = _missing_spouse_fields(application)
        if missing:
            raise loan_quotes.LoanQuoteError(
                code="spouse_info_required",
                message="Spouse information is required for married or domestic partner statuses",
                details={"missing_fields": missing},
            )

    selection_mode = LoanSelectionMode(application.selection_mode)
    selection_value = _selection_value_from_application(application)
    quote_request = LoanQuoteRequest(
        selection_mode=selection_mode,
        selection_value=selection_value,
        as_of_date=application.as_of_date,
        desired_interest_type=application.interest_type,
        desired_repayment_method=application.repayment_method,
        desired_term_months=application.term_months,
    )
    org_settings = await settings_service.get_org_settings(db, ctx)
    quote = await loan_quotes.calculate_loan_quote(db, ctx, membership, quote_request)
    _apply_quote(
        application,
        quote=quote,
        quote_request=quote_request,
        selection_mode=selection_mode,
        org_settings=org_settings,
    )
    application.status = LoanApplicationStatus.SUBMITTED.value
    if idempotency_key:
        application.submit_idempotency_key = idempotency_key

    db.add(application)
    await _ensure_core_workflow_stages(db, ctx, application)
    _record_audit_log(
        db=db,
        ctx=ctx,
        actor_id=actor_id,
        action="loan_application.submitted",
        application=application,
        old_value=old_snapshot,
    )
    await db.commit()
    await db.refresh(application)
    return application


async def cancel_draft_application(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application: LoanApplication,
    *,
    actor_id=None,
) -> LoanApplication:
    if application.status != LoanApplicationStatus.DRAFT.value:
        raise loan_quotes.LoanQuoteError(
            code="invalid_status",
            message="Only DRAFT applications can be cancelled",
            details={"status": application.status},
        )
    old_snapshot = _application_snapshot(application)
    application.status = LoanApplicationStatus.CANCELLED.value
    db.add(application)
    _record_audit_log(
        db=db,
        ctx=ctx,
        actor_id=actor_id,
        action="loan_application.cancelled",
        application=application,
        old_value=old_snapshot,
    )
    await db.commit()
    await db.refresh(application)
    return application
