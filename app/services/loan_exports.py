from __future__ import annotations

import csv
from io import StringIO
from decimal import Decimal

from app.models.loan_application import LoanApplication
from app.schemas.loan import LoanQuoteResponse, LoanScheduleResponse, LoanWhatIfRequest


def _stringify(value) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if value is None:
        return ""
    return str(value)


def _write_csv(headers: list[str], rows: list[list[str]]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def schedule_to_csv(schedule: LoanScheduleResponse) -> str:
    headers = [
        "period",
        "due_date",
        "payment",
        "principal",
        "interest",
        "remaining_balance",
    ]
    rows: list[list[str]] = []
    for entry in schedule.entries:
        rows.append(
            [
                str(entry.period),
                entry.due_date.isoformat() if entry.due_date else "",
                _stringify(entry.payment),
                _stringify(entry.principal),
                _stringify(entry.interest),
                _stringify(entry.remaining_balance),
            ]
        )
    return _write_csv(headers, rows)


def what_if_to_csv(request: LoanWhatIfRequest, quote: LoanQuoteResponse) -> str:
    headers = [
        "org_membership_id",
        "selection_mode",
        "selection_value",
        "as_of_date",
        "total_exercisable_shares",
        "shares_to_exercise",
        "purchase_price",
        "down_payment_amount",
        "loan_principal",
        "interest_type",
        "repayment_method",
        "term_months",
        "nominal_annual_rate",
        "estimated_monthly_payment",
        "total_payable",
        "total_interest",
    ]
    rows: list[list[str]] = []
    for option in quote.options:
        rows.append(
            [
                _stringify(request.org_membership_id),
                _stringify(quote.selection_mode),
                _stringify(quote.selection_value),
                quote.as_of_date.isoformat() if quote.as_of_date else "",
                str(quote.total_exercisable_shares),
                str(quote.shares_to_exercise),
                _stringify(quote.purchase_price),
                _stringify(quote.down_payment_amount),
                _stringify(quote.loan_principal),
                _stringify(option.interest_type),
                _stringify(option.repayment_method),
                str(option.term_months),
                _stringify(option.nominal_annual_rate),
                _stringify(option.estimated_monthly_payment),
                _stringify(option.total_payable),
                _stringify(option.total_interest),
            ]
        )
    return _write_csv(headers, rows)


def loan_export_to_csv(application: LoanApplication, schedule: LoanScheduleResponse) -> str:
    headers = [
        "loan_id",
        "status",
        "decision_reason",
        "as_of_date",
        "principal",
        "annual_rate_percent",
        "repayment_method",
        "term_months",
        "estimated_monthly_payment",
        "period",
        "due_date",
        "payment",
        "principal_payment",
        "interest_payment",
        "remaining_balance",
    ]
    rows: list[list[str]] = []
    for entry in schedule.entries:
        rows.append(
            [
                str(application.id),
                application.status,
                _stringify(application.decision_reason),
                application.as_of_date.isoformat() if application.as_of_date else "",
                _stringify(application.loan_principal),
                _stringify(application.nominal_annual_rate_percent),
                application.repayment_method,
                str(application.term_months),
                _stringify(application.estimated_monthly_payment),
                str(entry.period),
                entry.due_date.isoformat() if entry.due_date else "",
                _stringify(entry.payment),
                _stringify(entry.principal),
                _stringify(entry.interest),
                _stringify(entry.remaining_balance),
            ]
        )
    return _write_csv(headers, rows)
