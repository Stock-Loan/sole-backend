from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from decimal import Decimal
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, aliased

from app.api import deps
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.loan_application import LoanApplication
from app.models.loan_document import LoanDocument
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.models.department import Department
from app.models.org_settings import OrgSettings
from app.models.stock_grant_reservation import StockGrantReservation
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.common import MaritalStatus, normalize_marital_status
from app.schemas.loan import (
    LoanAdminEditRequest,
    LoanApplicationDraftCreate,
    LoanApplicationDraftUpdate,
    LoanApplicationStatus,
    LoanQuoteRequest,
    LoanSelectionMode,
)
from app.services import (
    loan_quotes,
    pbgc_rates,
    settings as settings_service,
    stock_reservations,
    vesting_engine,
)
from app.services.org_scoping import membership_join_condition, profile_join_condition
from app.services.audit import record_audit_log
from app.services.storage.adapter import GCSStorageAdapter, LocalFileSystemAdapter
from app.core.settings import settings


def _snapshot_org_settings(
    settings: OrgSettings,
    *,
    variable_base_rate_annual_percent: Decimal | None = None,
) -> dict:
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
    if variable_base_rate_annual_percent is not None:
        data["variable_base_rate_annual_percent"] = str(variable_base_rate_annual_percent)
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


def _current_policy_version(org_settings: OrgSettings) -> int:
    try:
        return int(org_settings.policy_version or 1)
    except (TypeError, ValueError):
        return 1


def _policy_version_mismatch(application: LoanApplication, current_version: int) -> bool:
    try:
        snapshot = (
            int(application.policy_version_snapshot)
            if application.policy_version_snapshot is not None
            else None
        )
    except (TypeError, ValueError):
        snapshot = None
    return snapshot != current_version


def _application_snapshot(application: LoanApplication) -> dict:
    return {
        "id": str(application.id) if application.id else None,
        "org_id": application.org_id,
        "org_membership_id": str(application.org_membership_id),
        "status": application.status,
        "decision_reason": application.decision_reason,
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
        "policy_version_snapshot": application.policy_version_snapshot,
        "interest_type": application.interest_type,
        "repayment_method": application.repayment_method,
        "term_months": application.term_months,
        "nominal_annual_rate_percent": _serialize_snapshot_value(
            application.nominal_annual_rate_percent
        ),
        "estimated_monthly_payment": _serialize_snapshot_value(
            application.estimated_monthly_payment
        ),
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


def _compute_workflow_flags(
    application: LoanApplication,
) -> tuple[bool | None, bool | None, int | None]:
    if application.activation_date is None or application.election_83b_due_date is None:
        return None, None, None
    documents = application.documents or []
    has_share_certificate = any(doc.document_type == "SHARE_CERTIFICATE" for doc in documents)
    has_83b_election = any(doc.document_type == "SECTION_83B_ELECTION" for doc in documents)
    days_until = (application.election_83b_due_date - date.today()).days
    return has_share_certificate, has_83b_election, days_until


def _quote_inputs_snapshot(request: LoanQuoteRequest) -> dict:
    return {
        "selection_mode": (
            request.selection_mode.value
            if isinstance(request.selection_mode, LoanSelectionMode)
            else str(request.selection_mode)
        ),
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
    record_audit_log(
        db,
        ctx,
        actor_id=actor_id,
        action=action,
        resource_type="loan_application",
        resource_id=str(application.id),
        old_value=old_value,
        new_value=_application_snapshot(application),
    )


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


async def _reserve_shares_for_application(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership: OrgMembership,
    application: LoanApplication,
    *,
    allocation,
    as_of_date: date,
) -> None:
    if not allocation:
        return
    grant_ids = [item.grant_id for item in allocation]
    if not grant_ids:
        return
    existing_stmt = select(StockGrantReservation.id).where(
        StockGrantReservation.org_id == ctx.org_id,
        StockGrantReservation.loan_application_id == application.id,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing:
        return
    grants_stmt = (
        select(EmployeeStockGrant)
        .options(selectinload(EmployeeStockGrant.vesting_events))
        .where(
            EmployeeStockGrant.org_id == ctx.org_id,
            EmployeeStockGrant.org_membership_id == membership.id,
            EmployeeStockGrant.id.in_(grant_ids),
            EmployeeStockGrant.status == "ACTIVE",
        )
        .with_for_update()
    )
    grants = (await db.execute(grants_stmt)).scalars().all()
    grants_by_id = {grant.id: grant for grant in grants}
    reserved_by_grant = await stock_reservations.get_active_reservations_by_grant(
        db, ctx, membership_id=membership.id, grant_ids=grant_ids
    )
    for item in allocation:
        grant = grants_by_id.get(item.grant_id)
        if not grant:
            raise loan_quotes.LoanQuoteError(
                code="grant_not_found",
                message="Grant not found for reservation",
                details={"grant_id": str(item.grant_id)},
            )
        vested, _ = vesting_engine.compute_grant_vesting(grant, as_of_date)
        reserved = reserved_by_grant.get(grant.id, 0)
        available = vested - reserved
        if item.shares > available:
            raise loan_quotes.LoanQuoteError(
                code="insufficient_available_shares",
                message="Not enough available vested shares to reserve",
                details={
                    "grant_id": str(item.grant_id),
                    "available_vested_shares": max(available, 0),
                    "requested_shares": int(item.shares),
                },
            )
    for item in allocation:
        db.add(
            StockGrantReservation(
                org_id=ctx.org_id,
                org_membership_id=membership.id,
                grant_id=item.grant_id,
                loan_application_id=application.id,
                shares_reserved=int(item.shares),
                status=application.status,
            )
        )


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
    variable_base_rate_annual_percent: Decimal | None = None,
) -> None:
    option = quote.options[0]
    selection_mode_value = (
        selection_mode.value
        if isinstance(selection_mode, LoanSelectionMode)
        else str(selection_mode)
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
    application.policy_version_snapshot = _current_policy_version(org_settings)
    application.interest_type = _enum_value(option.interest_type) or str(option.interest_type)
    application.repayment_method = _enum_value(option.repayment_method) or str(
        option.repayment_method
    )
    application.term_months = option.term_months
    application.nominal_annual_rate_percent = option.nominal_annual_rate
    application.estimated_monthly_payment = option.estimated_monthly_payment
    application.total_payable_amount = option.total_payable
    application.total_interest_amount = option.total_interest
    application.quote_inputs_snapshot = _quote_inputs_snapshot(quote_request)
    application.quote_option_snapshot = _quote_option_snapshot(option)
    application.allocation_strategy = quote.allocation_strategy
    application.allocation_snapshot = _allocation_snapshot(quote.allocation)
    application.org_settings_snapshot = _snapshot_org_settings(
        org_settings,
        variable_base_rate_annual_percent=variable_base_rate_annual_percent,
    )
    application.eligibility_result_snapshot = quote.eligibility_result.model_dump(mode="json")


async def get_membership_for_user(
    db: AsyncSession, ctx: deps.TenantContext, user_id
) -> OrgMembership | None:
    stmt = (
        select(OrgMembership)
        .options(selectinload(OrgMembership.profile))
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == user_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_membership_by_id(
    db: AsyncSession, ctx: deps.TenantContext, membership_id
) -> OrgMembership | None:
    stmt = select(OrgMembership).where(
        OrgMembership.org_id == ctx.org_id,
        OrgMembership.id == membership_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_membership_with_user(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership_id,
) -> tuple[OrgMembership, User, Department | None, OrgUserProfile | None] | None:
    stmt = (
        select(OrgMembership, User, Department, OrgUserProfile)
        .join(User, User.id == OrgMembership.user_id)
        .outerjoin(Department, Department.id == OrgMembership.department_id)
        .outerjoin(
            OrgUserProfile,
            profile_join_condition(OrgMembership, OrgUserProfile),
        )
        .where(
            OrgMembership.org_id == ctx.org_id,
            OrgMembership.id == membership_id,
        )
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        return None
    membership, user, department, profile = row
    return membership, user, department, profile


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
            selectinload(LoanApplication.documents).selectinload(LoanDocument.uploaded_by_user),
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


async def list_admin_applications(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    limit: int,
    offset: int,
    statuses: list[LoanApplicationStatus] | list[str] | None = None,
    stage_type: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> tuple[list[tuple], int]:
    if statuses:
        statuses = _normalize_status_values(statuses)
    stage_type = _normalize_stage_type(stage_type)
    conditions = [LoanApplication.org_id == ctx.org_id]
    if statuses:
        conditions.append(LoanApplication.status.in_(statuses))
    if created_from is not None:
        conditions.append(LoanApplication.created_at >= created_from)
    if created_to is not None:
        conditions.append(LoanApplication.created_at <= created_to)

    assigned_user = aliased(User)
    assigned_membership = aliased(OrgMembership)
    applicant_profile = aliased(OrgUserProfile)
    assigned_profile = aliased(OrgUserProfile)
    stage_subq = (
        select(
            LoanWorkflowStage.loan_application_id.label("loan_id"),
            LoanWorkflowStage.stage_type.label("stage_type"),
            LoanWorkflowStage.status.label("stage_status"),
            LoanWorkflowStage.assigned_to_user_id.label("assigned_to_user_id"),
            LoanWorkflowStage.assigned_at.label("assigned_at"),
        )
        .where(
            LoanWorkflowStage.org_id == ctx.org_id,
            LoanWorkflowStage.status != "COMPLETED",
        )
        .order_by(LoanWorkflowStage.loan_application_id, LoanWorkflowStage.created_at)
        .distinct(LoanWorkflowStage.loan_application_id)
        .subquery()
    )

    if stage_type:
        conditions.extend(
            [
                LoanWorkflowStage.org_id == ctx.org_id,
                LoanWorkflowStage.loan_application_id == LoanApplication.id,
                LoanWorkflowStage.stage_type == stage_type,
            ]
        )
        count_stmt = (
            select(func.count(func.distinct(LoanApplication.id)))
            .select_from(LoanApplication)
            .join(LoanWorkflowStage, LoanWorkflowStage.loan_application_id == LoanApplication.id)
            .where(*conditions)
        )
        count_result = await db.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)

        stmt = (
            select(
                LoanApplication,
                OrgMembership,
                User,
                Department,
                stage_subq.c.stage_type,
                stage_subq.c.stage_status,
                assigned_user,
                stage_subq.c.assigned_at,
                applicant_profile,
                assigned_profile,
            )
            .join(LoanWorkflowStage, LoanWorkflowStage.loan_application_id == LoanApplication.id)
            .join(
                OrgMembership,
                membership_join_condition(
                    OrgMembership, LoanApplication.org_id, LoanApplication.org_membership_id
                ),
            )
            .join(User, User.id == OrgMembership.user_id)
            .outerjoin(Department, Department.id == OrgMembership.department_id)
            .outerjoin(
                applicant_profile,
                profile_join_condition(OrgMembership, applicant_profile),
            )
            .outerjoin(stage_subq, stage_subq.c.loan_id == LoanApplication.id)
            .outerjoin(
                assigned_user,
                (assigned_user.id == stage_subq.c.assigned_to_user_id)
                & (assigned_user.org_id == ctx.org_id),
            )
            .outerjoin(
                assigned_membership,
                (assigned_membership.user_id == assigned_user.id)
                & (assigned_membership.org_id == ctx.org_id),
            )
            .outerjoin(
                assigned_profile,
                profile_join_condition(assigned_membership, assigned_profile),
            )
            .where(*conditions)
            .order_by(LoanApplication.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    else:
        count_stmt = select(func.count()).select_from(LoanApplication).where(*conditions)
        count_result = await db.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)

        stmt = (
            select(
                LoanApplication,
                OrgMembership,
                User,
                Department,
                stage_subq.c.stage_type,
                stage_subq.c.stage_status,
                assigned_user,
                stage_subq.c.assigned_at,
                applicant_profile,
                assigned_profile,
            )
            .join(
                OrgMembership,
                membership_join_condition(
                    OrgMembership, LoanApplication.org_id, LoanApplication.org_membership_id
                ),
            )
            .join(User, User.id == OrgMembership.user_id)
            .outerjoin(Department, Department.id == OrgMembership.department_id)
            .outerjoin(
                applicant_profile,
                profile_join_condition(OrgMembership, applicant_profile),
            )
            .outerjoin(stage_subq, stage_subq.c.loan_id == LoanApplication.id)
            .outerjoin(
                assigned_user,
                (assigned_user.id == stage_subq.c.assigned_to_user_id)
                & (assigned_user.org_id == ctx.org_id),
            )
            .outerjoin(
                assigned_membership,
                (assigned_membership.user_id == assigned_user.id)
                & (assigned_membership.org_id == ctx.org_id),
            )
            .outerjoin(
                assigned_profile,
                profile_join_condition(assigned_membership, assigned_profile),
            )
            .where(*conditions)
            .order_by(LoanApplication.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

    result = await db.execute(stmt)
    rows = result.all()
    return rows, total


def _normalize_status_values(statuses: list[LoanApplicationStatus] | list[str]) -> list[str]:
    values: list[str] = []
    for status in statuses:
        if isinstance(status, LoanApplicationStatus):
            values.append(status.value)
        else:
            values.append(str(status))
    return values


def _normalize_stage_type(stage_type: str | None) -> str | None:
    if stage_type is None:
        return None
    return stage_type.value if hasattr(stage_type, "value") else str(stage_type)


def _validate_admin_status_transition(current_status: str, next_status: str) -> None:
    if next_status == current_status:
        return
    if next_status == LoanApplicationStatus.IN_REVIEW.value:
        if current_status not in {
            LoanApplicationStatus.SUBMITTED.value,
            LoanApplicationStatus.IN_REVIEW.value,
        }:
            raise ValueError("Only SUBMITTED loans can transition to IN_REVIEW")
        return
    if next_status == LoanApplicationStatus.REJECTED.value:
        if current_status not in {
            LoanApplicationStatus.SUBMITTED.value,
            LoanApplicationStatus.IN_REVIEW.value,
        }:
            raise ValueError("Only SUBMITTED or IN_REVIEW loans can be rejected")
        return
    raise ValueError("Only IN_REVIEW or REJECTED status updates are supported")


async def update_admin_application_fields(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application: LoanApplication,
    payload: LoanAdminEditRequest,
    *,
    actor_id,
) -> LoanApplication:
    original_status = application.status
    editable_statuses = {
        LoanApplicationStatus.DRAFT.value,
        LoanApplicationStatus.SUBMITTED.value,
        LoanApplicationStatus.IN_REVIEW.value,
    }
    if application.status not in editable_statuses:
        raise loan_quotes.LoanQuoteError(
            code="invalid_status",
            message="Only DRAFT, SUBMITTED, or IN_REVIEW applications can be edited",
            details={"status": application.status},
        )

    membership = await get_membership_by_id(db, ctx, application.org_membership_id)
    if not membership:
        raise loan_quotes.LoanQuoteError(
            code="membership_not_found",
            message="Membership not found",
            details={"membership_id": str(application.org_membership_id)},
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
    org_settings = await settings_service.get_org_settings(db, ctx)
    variable_base_rate = await pbgc_rates.get_latest_annual_rate(db)
    current_policy_version = _current_policy_version(org_settings)
    if not recalc and _policy_version_mismatch(application, current_policy_version):
        raise loan_quotes.LoanQuoteError(
            code="policy_out_of_date",
            message="Loan policy changed. Please refresh the quote before editing.",
            details={
                "policy_version": current_policy_version,
                "policy_version_snapshot": application.policy_version_snapshot,
            },
        )
    workflow_reset = False
    documents_deleted = 0
    if recalc:
        target_status = original_status
        if payload.reset_workflow and application.status != LoanApplicationStatus.DRAFT.value:
            target_status = LoanApplicationStatus.IN_REVIEW.value
        if payload.reset_workflow:
            workflow_reset = True
        selection_mode = payload.selection_mode or LoanSelectionMode(application.selection_mode)
        selection_value = (
            payload.selection_value
            if payload.selection_value is not None
            else _selection_value_from_application(application)
        )
        quote_request = LoanQuoteRequest(
            selection_mode=selection_mode,
            selection_value=selection_value,
            as_of_date=payload.as_of_date or application.as_of_date,
            desired_interest_type=payload.desired_interest_type or application.interest_type,
            desired_repayment_method=payload.desired_repayment_method
            or application.repayment_method,
            desired_term_months=payload.desired_term_months or application.term_months,
        )
        quote = await loan_quotes.calculate_loan_quote(db, ctx, membership, quote_request)
        _apply_quote(
            application,
            quote=quote,
            quote_request=quote_request,
            selection_mode=selection_mode,
            org_settings=org_settings,
            variable_base_rate_annual_percent=variable_base_rate,
        )
        application.status = target_status
        if (
            original_status
            in {
                LoanApplicationStatus.SUBMITTED.value,
                LoanApplicationStatus.IN_REVIEW.value,
            }
            or workflow_reset
        ):
            await stock_reservations.delete_reservations_for_application(
                db, ctx, application_id=application.id
            )
            if payload.delete_documents:
                documents_deleted = await _delete_loan_documents(db, ctx, application.id)
            if payload.reset_workflow:
                await _reset_workflow_stages(db, ctx, application.id)
            if payload.delete_documents or payload.reset_workflow:
                db.expire(application, ["documents", "workflow_stages"])
            await db.flush()
            await _reserve_shares_for_application(
                db,
                ctx,
                membership,
                application,
                allocation=quote.allocation,
                as_of_date=date.today(),
            )
    else:
        if payload.delete_documents:
            documents_deleted = await _delete_loan_documents(db, ctx, application.id)
            db.expire(application, ["documents"])
        if payload.reset_workflow:
            if application.status != LoanApplicationStatus.DRAFT.value:
                application.status = LoanApplicationStatus.IN_REVIEW.value
            workflow_reset = True
            await _reset_workflow_stages(db, ctx, application.id)
            db.expire(application, ["workflow_stages"])

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

    await db.flush()
    await db.refresh(application)
    new_snapshot = _application_snapshot(application)
    new_snapshot.update(
        {
            "edit_note": payload.note,
            "workflow_reset": workflow_reset,
            "documents_deleted": documents_deleted,
            "delete_documents": payload.delete_documents,
            "reset_workflow": payload.reset_workflow,
        }
    )
    record_audit_log(
        db,
        ctx,
        actor_id=actor_id,
        action="loan_application.admin_edit",
        resource_type="loan_application",
        resource_id=str(application.id),
        old_value=old_snapshot,
        new_value=new_snapshot,
    )
    await db.flush()
    await db.refresh(application)
    return application


def _adapter_for_document(provider: str | None, bucket: str | None):
    if (provider or "").lower() == "gcs":
        resolved_bucket = bucket or settings.gcs_bucket
        if not resolved_bucket:
            return None
        return GCSStorageAdapter(
            bucket=resolved_bucket,
            signed_url_expiry_seconds=settings.gcs_signed_url_expiry_seconds,
        )
    return LocalFileSystemAdapter(
        base_path=settings.local_upload_dir, base_url=settings.public_base_url
    )


async def _delete_loan_documents(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id: UUID,
) -> int:
    doc_stmt = select(LoanDocument).where(
        LoanDocument.org_id == ctx.org_id,
        LoanDocument.loan_application_id == loan_id,
    )
    documents = (await db.execute(doc_stmt)).scalars().all()
    deleted = 0
    for doc in documents:
        object_key = doc.storage_key or doc.storage_path_or_url
        if object_key and "://" not in object_key:
            try:
                adapter = _adapter_for_document(doc.storage_provider, doc.storage_bucket)
                if adapter:
                    adapter.delete_object(object_key)
            except Exception:
                # Best effort cleanup; keep going so edits are not blocked.
                pass
        deleted += 1

    await db.execute(
        delete(LoanDocument)
        .where(
            LoanDocument.org_id == ctx.org_id,
            LoanDocument.loan_application_id == loan_id,
        )
        .execution_options(synchronize_session=False)
    )
    return int(deleted or 0)


async def _reset_workflow_stages(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id: UUID,
) -> None:
    await db.execute(
        delete(LoanWorkflowStage)
        .where(
            LoanWorkflowStage.org_id == ctx.org_id,
            LoanWorkflowStage.loan_application_id == loan_id,
        )
        .execution_options(synchronize_session=False)
    )
    await _ensure_core_workflow_stages(db, ctx, LoanApplication(id=loan_id, org_id=ctx.org_id))
    return None


async def update_admin_application(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application: LoanApplication,
    *,
    next_status: LoanApplicationStatus,
    decision_reason: str | None,
    actor_id,
) -> LoanApplication:
    old_snapshot = _application_snapshot(application)
    next_value = (
        next_status.value if isinstance(next_status, LoanApplicationStatus) else str(next_status)
    )
    _validate_admin_status_transition(application.status, next_value)

    application.status = next_value
    if decision_reason is not None:
        application.decision_reason = decision_reason

    if next_value in {LoanApplicationStatus.IN_REVIEW.value, LoanApplicationStatus.REJECTED.value}:
        await stock_reservations.set_reservation_status_for_application(
            db,
            ctx,
            application_id=application.id,
            status=next_value,
        )

    _record_audit_log(
        db=db,
        ctx=ctx,
        actor_id=actor_id,
        action="loan_application.admin_update",
        application=application,
        old_value=old_snapshot,
    )
    await db.flush()
    await db.refresh(application)
    if next_value == LoanApplicationStatus.REJECTED.value:
        from app.services import stock_dashboard, stock_summary

        await stock_summary.invalidate_stock_summary_cache(
            ctx.org_id, application.org_membership_id
        )
        await stock_dashboard.invalidate_stock_dashboard_cache(ctx.org_id)
    return application


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
    variable_base_rate = await pbgc_rates.get_latest_annual_rate(db)
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
        variable_base_rate_annual_percent=variable_base_rate,
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
        await db.flush()
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
    org_settings = await settings_service.get_org_settings(db, ctx)
    variable_base_rate = await pbgc_rates.get_latest_annual_rate(db)
    current_policy_version = _current_policy_version(org_settings)
    if not recalc and _policy_version_mismatch(application, current_policy_version):
        raise loan_quotes.LoanQuoteError(
            code="policy_out_of_date",
            message="Loan policy changed. Please refresh your quote before continuing.",
            details={
                "policy_version": current_policy_version,
                "policy_version_snapshot": application.policy_version_snapshot,
            },
        )
    if recalc:
        selection_mode = payload.selection_mode or LoanSelectionMode(application.selection_mode)
        selection_value = (
            payload.selection_value
            if payload.selection_value is not None
            else _selection_value_from_application(application)
        )
        quote_request = LoanQuoteRequest(
            selection_mode=selection_mode,
            selection_value=selection_value,
            as_of_date=payload.as_of_date or application.as_of_date,
            desired_interest_type=payload.desired_interest_type or application.interest_type,
            desired_repayment_method=payload.desired_repayment_method
            or application.repayment_method,
            desired_term_months=payload.desired_term_months or application.term_months,
        )
        quote = await loan_quotes.calculate_loan_quote(db, ctx, membership, quote_request)
        _apply_quote(
            application,
            quote=quote,
            quote_request=quote_request,
            selection_mode=selection_mode,
            org_settings=org_settings,
            variable_base_rate_annual_percent=variable_base_rate,
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
    await db.flush()
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
            await db.flush()
            await db.refresh(application)
            return application
        raise loan_quotes.LoanQuoteError(
            code="invalid_status",
            message="Only DRAFT applications can be submitted",
            details={"status": application.status},
        )

    if (
        idempotency_key
        and application.submit_idempotency_key
        and application.submit_idempotency_key != idempotency_key
    ):
        raise loan_quotes.LoanQuoteError(
            code="idempotency_conflict",
            message="Submission already processed with a different idempotency key",
            details={"submitted_key": application.submit_idempotency_key},
        )

    old_snapshot = _application_snapshot(application)

    profile = membership.profile
    profile_marital = profile.marital_status if profile else None
    current_status = normalize_marital_status(profile_marital)
    submitted_status = normalize_marital_status(application.marital_status_snapshot)
    if current_status and submitted_status and current_status != submitted_status:
        raise loan_quotes.LoanQuoteError(
            code="marital_status_mismatch",
            message=(
                "Marital status does not match our records. "
                "Please contact HR to update your marital status before submitting."
            ),
            details={
                "current_status": profile_marital,
                "submitted_status": application.marital_status_snapshot,
            },
        )
    if current_status and not submitted_status:
        application.marital_status_snapshot = profile_marital
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
    variable_base_rate = await pbgc_rates.get_latest_annual_rate(db)
    current_policy_version = _current_policy_version(org_settings)
    if _policy_version_mismatch(application, current_policy_version):
        raise loan_quotes.LoanQuoteError(
            code="policy_out_of_date",
            message="Loan policy changed. Please refresh your quote before submitting.",
            details={
                "policy_version": current_policy_version,
                "policy_version_snapshot": application.policy_version_snapshot,
            },
        )
    quote = await loan_quotes.calculate_loan_quote(db, ctx, membership, quote_request)
    _apply_quote(
        application,
        quote=quote,
        quote_request=quote_request,
        selection_mode=selection_mode,
        org_settings=org_settings,
        variable_base_rate_annual_percent=variable_base_rate,
    )
    application.status = LoanApplicationStatus.SUBMITTED.value
    if idempotency_key:
        application.submit_idempotency_key = idempotency_key

    db.add(application)
    await _reserve_shares_for_application(
        db,
        ctx,
        membership,
        application,
        allocation=quote.allocation,
        as_of_date=date.today(),
    )
    await _ensure_core_workflow_stages(db, ctx, application)
    _record_audit_log(
        db=db,
        ctx=ctx,
        actor_id=actor_id,
        action="loan_application.submitted",
        application=application,
        old_value=old_snapshot,
    )
    await db.flush()
    from app.services import stock_dashboard, stock_summary

    await stock_summary.invalidate_stock_summary_cache(ctx.org_id, membership.id)
    await stock_dashboard.invalidate_stock_dashboard_cache(ctx.org_id)
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
    await db.flush()
    await db.refresh(application)
    return application
