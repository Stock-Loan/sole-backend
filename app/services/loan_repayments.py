from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.loan_application import LoanApplication
from app.models.loan_repayment import LoanRepayment
from app.schemas.loan import LoanApplicationStatus, LoanRepaymentCreateRequest
from app.services.audit import model_snapshot, record_audit_log


TWOPLACES = Decimal("0.01")


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def record_repayment(
    db: AsyncSession,
    ctx: deps.TenantContext,
    application: LoanApplication,
    payload: LoanRepaymentCreateRequest,
    *,
    actor_id,
    evidence_file_name: str | None = None,
    evidence_storage_path_or_url: str | None = None,
    evidence_content_type: str | None = None,
) -> LoanRepayment:
    if application.status not in {
        LoanApplicationStatus.ACTIVE.value,
        LoanApplicationStatus.COMPLETED.value,
    }:
        raise ValueError("Repayments can only be recorded for ACTIVE or COMPLETED loans")

    principal = _as_decimal(payload.principal_amount).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    interest = _as_decimal(payload.interest_amount).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    amount = _as_decimal(payload.amount).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    diff = (principal + interest - amount).copy_abs()
    if diff > TWOPLACES:
        raise ValueError("principal_amount + interest_amount must equal amount")

    repayment = LoanRepayment(
        org_id=ctx.org_id,
        loan_application_id=application.id,
        amount=amount,
        principal_amount=principal,
        interest_amount=interest,
        payment_date=payload.payment_date,
        recorded_by_user_id=actor_id,
        evidence_file_name=evidence_file_name,
        evidence_storage_path_or_url=evidence_storage_path_or_url,
        evidence_content_type=evidence_content_type,
    )
    db.add(repayment)
    await db.flush()

    paid_stmt = select(func.coalesce(func.sum(LoanRepayment.amount), 0)).where(
        LoanRepayment.org_id == ctx.org_id,
        LoanRepayment.loan_application_id == application.id,
    )
    total_paid = _as_decimal((await db.execute(paid_stmt)).scalar_one())
    total_due = _as_decimal(application.total_payable_amount)

    if application.status != LoanApplicationStatus.COMPLETED.value and total_paid >= total_due:
        old_snapshot = model_snapshot(application)
        application.status = LoanApplicationStatus.COMPLETED.value
        db.add(application)
        record_audit_log(
            db,
            ctx,
            actor_id=actor_id,
            action="loan_application.completed",
            resource_type="loan_application",
            resource_id=str(application.id),
            old_value=old_snapshot,
            new_value=model_snapshot(application),
        )

    record_audit_log(
        db,
        ctx,
        actor_id=actor_id,
        action="loan_repayment.recorded",
        resource_type="loan_repayment",
        resource_id=str(repayment.id),
        old_value=None,
        new_value=model_snapshot(repayment),
    )
    return repayment


async def list_repayments(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id,
) -> list[LoanRepayment]:
    stmt = (
        select(LoanRepayment)
        .options(selectinload(LoanRepayment.recorded_by_user))
        .where(
            LoanRepayment.org_id == ctx.org_id,
            LoanRepayment.loan_application_id == loan_id,
        )
        .order_by(LoanRepayment.payment_date.desc(), LoanRepayment.created_at.desc())
    )
    return (await db.execute(stmt)).scalars().all()


async def list_repayments_up_to(
    db: AsyncSession,
    ctx: deps.TenantContext,
    loan_id,
    *,
    as_of_date,
) -> list[LoanRepayment]:
    stmt = (
        select(LoanRepayment)
        .options(selectinload(LoanRepayment.recorded_by_user))
        .where(
            LoanRepayment.org_id == ctx.org_id,
            LoanRepayment.loan_application_id == loan_id,
            LoanRepayment.payment_date <= as_of_date,
        )
        .order_by(LoanRepayment.payment_date.asc(), LoanRepayment.created_at.asc())
    )
    return (await db.execute(stmt)).scalars().all()


async def sum_repayments_for_org(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    statuses: list[str] | None = None,
) -> tuple[Decimal, Decimal]:
    stmt = (
        select(
            func.coalesce(func.sum(LoanRepayment.amount), 0),
            func.coalesce(func.sum(LoanRepayment.interest_amount), 0),
        )
        .select_from(LoanRepayment)
        .join(LoanApplication, LoanApplication.id == LoanRepayment.loan_application_id)
        .where(LoanRepayment.org_id == ctx.org_id)
    )
    if statuses:
        stmt = stmt.where(LoanApplication.status.in_(statuses))
    row = (await db.execute(stmt)).first()
    if not row:
        return Decimal("0.00"), Decimal("0.00")
    amount_sum, interest_sum = row
    return _as_decimal(amount_sum), _as_decimal(interest_sum)
