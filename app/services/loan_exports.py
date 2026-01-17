from __future__ import annotations

import csv
from io import StringIO
from decimal import Decimal

from app.models.loan_application import LoanApplication
from app.schemas.loan import LoanScheduleResponse


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
