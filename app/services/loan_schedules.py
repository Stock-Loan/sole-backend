from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.models.loan_application import LoanApplication
from app.schemas.loan import LoanScheduleEntry, LoanScheduleResponse, LoanScheduleWhatIfRequest
from app.schemas.settings import LoanRepaymentMethod


TWOPLACES = Decimal("0.01")


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _monthly_rate(annual_rate_percent: Decimal) -> Decimal:
    return annual_rate_percent / Decimal("1200")


def _payment_principal_and_interest(principal: Decimal, annual_rate_percent: Decimal, term_months: int) -> Decimal:
    if term_months <= 0:
        return Decimal("0.00")
    rate = _monthly_rate(annual_rate_percent)
    if rate == 0:
        return (principal / Decimal(term_months)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    factor = (Decimal("1") + rate) ** term_months
    payment = principal * rate * factor / (factor - Decimal("1"))
    return payment.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def build_schedule(application: LoanApplication) -> LoanScheduleResponse:
    principal = _as_decimal(application.loan_principal)
    annual_rate = _as_decimal(application.nominal_annual_rate_percent)
    term_months = int(application.term_months or 0)
    repayment_method = LoanRepaymentMethod(application.repayment_method)
    start_date = (
        application.activation_date.date()
        if application.activation_date is not None
        else application.as_of_date
    )
    return _build_schedule_from_terms(
        loan_id=application.id,
        principal=principal,
        annual_rate=annual_rate,
        term_months=term_months,
        repayment_method=repayment_method,
        start_date=start_date,
    )


def build_schedule_what_if(
    application: LoanApplication,
    payload: LoanScheduleWhatIfRequest,
) -> LoanScheduleResponse:
    principal = _as_decimal(payload.principal if payload.principal is not None else application.loan_principal)
    annual_rate = _as_decimal(
        payload.annual_rate_percent
        if payload.annual_rate_percent is not None
        else application.nominal_annual_rate_percent
    )
    term_months = int(payload.term_months if payload.term_months is not None else application.term_months or 0)
    repayment_method = LoanRepaymentMethod(
        payload.repayment_method if payload.repayment_method is not None else application.repayment_method
    )
    start_date = (
        payload.as_of_date
        if payload.as_of_date is not None
        else (
            application.activation_date.date()
            if application.activation_date is not None
            else application.as_of_date
        )
    )
    return _build_schedule_from_terms(
        loan_id=application.id,
        principal=principal,
        annual_rate=annual_rate,
        term_months=term_months,
        repayment_method=repayment_method,
        start_date=start_date,
    )


def _estimate_monthly_payment(
    principal: Decimal, annual_rate: Decimal, term_months: int, repayment_method: LoanRepaymentMethod
) -> Decimal:
    if repayment_method == LoanRepaymentMethod.PRINCIPAL_AND_INTEREST:
        return _payment_principal_and_interest(principal, annual_rate, term_months)
    monthly_interest = (principal * _monthly_rate(annual_rate)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    return monthly_interest


def _build_schedule_from_terms(
    *,
    loan_id,
    principal: Decimal,
    annual_rate: Decimal,
    term_months: int,
    repayment_method: LoanRepaymentMethod,
    start_date: date,
) -> LoanScheduleResponse:
    if term_months <= 0:
        raise ValueError("term_months must be >= 1")

    balance = principal
    entries: list[LoanScheduleEntry] = []

    if repayment_method == LoanRepaymentMethod.PRINCIPAL_AND_INTEREST:
        monthly_payment = _payment_principal_and_interest(principal, annual_rate, term_months)
        for period in range(1, term_months + 1):
            interest = (balance * _monthly_rate(annual_rate)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
            principal_payment = (monthly_payment - interest).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
            if period == term_months:
                principal_payment = balance
                monthly_payment = (principal_payment + interest).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
            balance = (balance - principal_payment).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
            entries.append(
                LoanScheduleEntry(
                    period=period,
                    due_date=_add_months(start_date, period),
                    payment=monthly_payment,
                    principal=principal_payment,
                    interest=interest,
                    remaining_balance=balance,
                )
            )
    else:
        monthly_interest = (principal * _monthly_rate(annual_rate)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        for period in range(1, term_months + 1):
            principal_payment = Decimal("0.00")
            payment = monthly_interest
            if period == term_months:
                principal_payment = balance
                payment = (monthly_interest + principal_payment).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
            balance = (balance - principal_payment).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
            entries.append(
                LoanScheduleEntry(
                    period=period,
                    due_date=_add_months(start_date, period),
                    payment=payment,
                    principal=principal_payment,
                    interest=monthly_interest,
                    remaining_balance=balance,
                )
            )

    return LoanScheduleResponse(
        loan_id=loan_id,
        as_of_date=start_date,
        repayment_method=repayment_method,
        term_months=term_months,
        principal=principal,
        annual_rate_percent=annual_rate,
        estimated_monthly_payment=_estimate_monthly_payment(
            principal, annual_rate, term_months, repayment_method
        ),
        entries=entries,
    )
