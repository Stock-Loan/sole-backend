from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.loan_application import LoanApplication
from app.models.loan_repayment import LoanRepayment
from app.services import loan_schedules


@dataclass(frozen=True)
class LoanPaymentStatus:
    next_payment_date: date | None
    next_payment_amount: Decimal | None
    next_principal_due: Decimal | None
    next_interest_due: Decimal | None
    missed_payment_count: int
    missed_payment_amount_total: Decimal
    missed_payment_dates: list[date]
    principal_remaining: Decimal
    interest_remaining: Decimal
    total_remaining: Decimal


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def compute_payment_status(
    application: LoanApplication,
    repayments: list[LoanRepayment],
    as_of_date: date,
) -> LoanPaymentStatus:
    schedule = loan_schedules.build_schedule(application)
    entries = schedule.entries
    remaining_principal = [_as_decimal(entry.principal) for entry in entries]
    remaining_interest = [_as_decimal(entry.interest) for entry in entries]

    repayments_sorted = sorted(
        repayments,
        key=lambda item: (item.payment_date, item.created_at),
    )

    for repayment in repayments_sorted:
        principal_payment = _as_decimal(repayment.principal_amount)
        for idx, remaining in enumerate(remaining_principal):
            if principal_payment <= 0:
                break
            if remaining <= 0:
                continue
            applied = min(principal_payment, remaining)
            remaining_principal[idx] = remaining - applied
            principal_payment -= applied

        interest_payment = _as_decimal(repayment.interest_amount)
        for idx, remaining in enumerate(remaining_interest):
            if interest_payment <= 0:
                break
            if remaining <= 0:
                continue
            applied = min(interest_payment, remaining)
            remaining_interest[idx] = remaining - applied
            interest_payment -= applied

    next_payment_date = None
    next_payment_amount = None
    next_principal_due = None
    next_interest_due = None
    missed_dates: list[date] = []
    missed_total = Decimal("0")

    for entry, principal_due, interest_due in zip(entries, remaining_principal, remaining_interest):
        remaining_total = principal_due + interest_due
        if remaining_total <= 0:
            continue
        if next_payment_date is None:
            next_payment_date = entry.due_date
            next_payment_amount = remaining_total
            next_principal_due = principal_due
            next_interest_due = interest_due
        if entry.due_date and entry.due_date < as_of_date:
            missed_dates.append(entry.due_date)
            missed_total += remaining_total

    principal_remaining = sum(remaining_principal, Decimal("0"))
    interest_remaining = sum(remaining_interest, Decimal("0"))
    total_remaining = principal_remaining + interest_remaining

    return LoanPaymentStatus(
        next_payment_date=next_payment_date,
        next_payment_amount=next_payment_amount,
        next_principal_due=next_principal_due,
        next_interest_due=next_interest_due,
        missed_payment_count=len(missed_dates),
        missed_payment_amount_total=missed_total,
        missed_payment_dates=missed_dates,
        principal_remaining=principal_remaining,
        interest_remaining=interest_remaining,
        total_remaining=total_remaining,
    )
