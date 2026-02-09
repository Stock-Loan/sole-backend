from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.models.vesting_event import VestingEvent
from app.schemas.stock import EligibilityReasonCode
from app.services import stock_summary


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
        min_service_duration_years=None,
        enforce_min_vested_to_exercise=False,
        min_vested_shares_to_exercise=None,
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


def test_summary_no_grants():
    membership = _membership()
    settings = _settings()
    summary = stock_summary.build_stock_summary_from_data(
        membership=membership,
        org_settings=settings,
        grants=[],
        as_of_date=date.today(),
    )
    assert summary.total_granted_shares == 0
    assert summary.total_vested_shares == 0
    assert summary.total_unvested_shares == 0
    assert summary.eligibility_result.eligible_to_exercise is False
    assert any(
        reason.code == EligibilityReasonCode.NO_VESTED_SHARES
        for reason in summary.eligibility_result.reasons
    )


def test_summary_partial_vesting():
    membership = _membership()
    settings = _settings()
    grant = _grant(100, date(2025, 1, 1))
    grant.vesting_events = [
        _event(grant, date(2025, 6, 1), 50),
        _event(grant, date(2026, 1, 1), 50),
    ]
    summary = stock_summary.build_stock_summary_from_data(
        membership=membership,
        org_settings=settings,
        grants=[grant],
        as_of_date=date(2025, 7, 1),
    )
    assert summary.total_granted_shares == 100
    assert summary.total_vested_shares == 50
    assert summary.total_unvested_shares == 50
    assert summary.next_vesting_event is not None
    assert summary.next_vesting_event.vest_date == date(2026, 1, 1)
    assert summary.eligibility_result.eligible_to_exercise is True


def test_summary_ineligible_due_to_service_duration():
    membership = _membership(employment_start_date=date.today() - timedelta(days=30))
    settings = _settings(enforce_service_duration_rule=True, min_service_duration_years=0.5)
    grant = _grant(100, date.today())
    grant.vesting_events = [_event(grant, date.today(), 100)]
    summary = stock_summary.build_stock_summary_from_data(
        membership=membership,
        org_settings=settings,
        grants=[grant],
        as_of_date=date.today(),
    )
    assert summary.eligibility_result.eligible_to_exercise is False
    assert any(
        reason.code == EligibilityReasonCode.INSUFFICIENT_SERVICE_DURATION
        for reason in summary.eligibility_result.reasons
    )


def test_summary_ineligible_due_to_min_vested_threshold():
    membership = _membership()
    settings = _settings(enforce_min_vested_to_exercise=True, min_vested_shares_to_exercise=200)
    grant = _grant(150, date.today())
    grant.vesting_events = [_event(grant, date.today(), 150)]
    summary = stock_summary.build_stock_summary_from_data(
        membership=membership,
        org_settings=settings,
        grants=[grant],
        as_of_date=date.today(),
    )
    assert summary.eligibility_result.eligible_to_exercise is False
    assert any(
        reason.code == EligibilityReasonCode.BELOW_MIN_VESTED_THRESHOLD
        for reason in summary.eligibility_result.reasons
    )
