import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.models.vesting_event import VestingEvent
from app.schemas.loan import LoanQuoteRequest, LoanSelectionMode
from app.schemas.settings import LoanInterestType, LoanRepaymentMethod
from app.services import loan_quotes


def _membership(**overrides) -> OrgMembership:
    defaults = dict(
        org_id="default",
        user_id=uuid4(),
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
        employment_start_date=date.today() - timedelta(days=365),
    )
    defaults.update(overrides)
    return OrgMembership(**defaults)


def _settings(**overrides) -> OrgSettings:
    defaults = dict(
        org_id="default",
        allow_user_data_export=True,
        allow_profile_edit=True,
        require_two_factor=False,
        audit_log_retention_days=180,
        inactive_user_retention_days=180,
        enforce_service_duration_rule=False,
        min_service_duration_days=None,
        enforce_min_vested_to_exercise=False,
        min_vested_shares_to_exercise=None,
        allowed_repayment_methods=["INTEREST_ONLY"],
        min_loan_term_months=12,
        max_loan_term_months=60,
        allowed_interest_types=["FIXED"],
        fixed_interest_rate_annual_percent=Decimal("8.0"),
        variable_base_rate_annual_percent=None,
        variable_margin_annual_percent=None,
        require_down_payment=False,
        down_payment_percent=None,
    )
    defaults.update(overrides)
    return OrgSettings(**defaults)


def _grant(total_shares: int, grant_date: date) -> EmployeeStockGrant:
    grant = EmployeeStockGrant(
        id=uuid4(),
        org_id="default",
        org_membership_id=uuid4(),
        grant_date=grant_date,
        total_shares=total_shares,
        exercise_price=Decimal("1.25"),
        status="ACTIVE",
        vesting_strategy="SCHEDULED",
    )
    grant.vesting_events = []
    return grant


def _event(grant: EmployeeStockGrant, vest_date: date, shares: int) -> VestingEvent:
    return VestingEvent(
        id=uuid4(),
        org_id="default",
        grant_id=grant.id,
        vest_date=vest_date,
        shares=shares,
    )


def test_quote_shares_happy_path():
    membership = _membership()
    settings = _settings()
    grant = _grant(100, date(2025, 1, 1))
    grant.vesting_events = [_event(grant, date(2025, 1, 1), 100)]

    request = LoanQuoteRequest(
        selection_mode=LoanSelectionMode.SHARES,
        selection_value=Decimal("50"),
        desired_repayment_method=LoanRepaymentMethod.INTEREST_ONLY,
        desired_interest_type=LoanInterestType.FIXED,
        desired_term_months=12,
        as_of_date=date(2025, 2, 1),
    )

    result = loan_quotes.build_loan_quote_from_data(
        membership=membership,
        org_settings=settings,
        grants=[grant],
        request=request,
        as_of_date=request.as_of_date,
    )
    assert result.shares_to_exercise == 50
    assert result.total_exercisable_shares == 100
    assert result.purchase_price == Decimal("62.50")
    assert result.down_payment_amount == Decimal("0")
    assert result.loan_principal == Decimal("62.50")
    assert len(result.options) == 1


def test_quote_percent_selection():
    membership = _membership()
    settings = _settings()
    grant = _grant(200, date(2025, 1, 1))
    grant.vesting_events = [_event(grant, date(2025, 1, 1), 200)]
    request = LoanQuoteRequest(
        selection_mode=LoanSelectionMode.PERCENT,
        selection_value=Decimal("25"),
        desired_term_months=12,
        as_of_date=date(2025, 2, 1),
    )
    result = loan_quotes.build_loan_quote_from_data(
        membership=membership,
        org_settings=settings,
        grants=[grant],
        request=request,
        as_of_date=request.as_of_date,
    )
    assert result.shares_to_exercise == 50


def test_quote_rejects_shares_over_eligibility():
    membership = _membership()
    settings = _settings()
    grant = _grant(50, date(2025, 1, 1))
    grant.vesting_events = [_event(grant, date(2025, 1, 1), 50)]
    request = LoanQuoteRequest(
        selection_mode=LoanSelectionMode.SHARES,
        selection_value=Decimal("100"),
        desired_term_months=12,
        as_of_date=date(2025, 2, 1),
    )
    with pytest.raises(loan_quotes.LoanQuoteError) as exc_info:
        loan_quotes.build_loan_quote_from_data(
            membership=membership,
            org_settings=settings,
            grants=[grant],
            request=request,
            as_of_date=request.as_of_date,
        )
    assert exc_info.value.code == "shares_exceed_eligibility"


def test_quote_rejects_invalid_term():
    membership = _membership()
    settings = _settings(min_loan_term_months=12, max_loan_term_months=24)
    grant = _grant(100, date(2025, 1, 1))
    grant.vesting_events = [_event(grant, date(2025, 1, 1), 100)]
    request = LoanQuoteRequest(
        selection_mode=LoanSelectionMode.SHARES,
        selection_value=Decimal("50"),
        desired_term_months=36,
        as_of_date=date(2025, 2, 1),
    )
    with pytest.raises(loan_quotes.LoanQuoteError) as exc_info:
        loan_quotes.build_loan_quote_from_data(
            membership=membership,
            org_settings=settings,
            grants=[grant],
            request=request,
            as_of_date=request.as_of_date,
        )
    assert exc_info.value.code == "invalid_term"


def test_quote_rejects_ineligible_employee():
    membership = _membership(employment_status="TERMINATED")
    settings = _settings()
    grant = _grant(100, date(2025, 1, 1))
    grant.vesting_events = [_event(grant, date(2025, 1, 1), 100)]
    request = LoanQuoteRequest(
        selection_mode=LoanSelectionMode.SHARES,
        selection_value=Decimal("10"),
        desired_term_months=12,
        as_of_date=date(2025, 2, 1),
    )
    with pytest.raises(loan_quotes.LoanQuoteError) as exc_info:
        loan_quotes.build_loan_quote_from_data(
            membership=membership,
            org_settings=settings,
            grants=[grant],
            request=request,
            as_of_date=request.as_of_date,
        )
    assert exc_info.value.code == "exercise_ineligible"
