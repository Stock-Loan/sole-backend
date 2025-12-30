import os
from datetime import date, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.schemas.stock import EligibilityReasonCode
from app.services import eligibility
from app.services.vesting_engine import VestingTotals


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
    )
    defaults.update(overrides)
    return OrgSettings(**defaults)


def _membership(**overrides) -> OrgMembership:
    defaults = dict(
        org_id="default",
        user_id="00000000-0000-0000-0000-000000000000",
        employee_id="E-100",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
        employment_start_date=date.today() - timedelta(days=365),
    )
    defaults.update(overrides)
    return OrgMembership(**defaults)


def _totals(vested: int, unvested: int = 0, granted: int | None = None) -> VestingTotals:
    return VestingTotals(
        total_granted_shares=granted if granted is not None else vested + unvested,
        total_vested_shares=vested,
        total_unvested_shares=unvested,
        next_vesting_event=None,
    )


def test_eligible_when_requirements_met():
    settings = _settings(enforce_min_vested_to_exercise=True, min_vested_shares_to_exercise=100)
    membership = _membership()
    totals = _totals(vested=150, unvested=50)
    result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=settings,
        totals=totals,
        as_of_date=date.today(),
    )
    assert result.eligible_to_exercise is True
    assert result.reasons == []


def test_ineligible_when_employment_inactive():
    settings = _settings()
    membership = _membership(employment_status="TERMINATED")
    totals = _totals(vested=100)
    result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=settings,
        totals=totals,
        as_of_date=date.today(),
    )
    assert result.eligible_to_exercise is False
    assert any(r.code == EligibilityReasonCode.EMPLOYMENT_INACTIVE for r in result.reasons)


def test_ineligible_when_service_duration_short():
    settings = _settings(enforce_service_duration_rule=True, min_service_duration_days=365)
    membership = _membership(employment_start_date=date.today() - timedelta(days=30))
    totals = _totals(vested=100)
    result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=settings,
        totals=totals,
        as_of_date=date.today(),
    )
    assert result.eligible_to_exercise is False
    assert any(r.code == EligibilityReasonCode.INSUFFICIENT_SERVICE_DURATION for r in result.reasons)


def test_ineligible_when_below_min_vested_threshold():
    settings = _settings(enforce_min_vested_to_exercise=True, min_vested_shares_to_exercise=500)
    membership = _membership()
    totals = _totals(vested=100, unvested=400)
    result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=settings,
        totals=totals,
        as_of_date=date.today(),
    )
    assert result.eligible_to_exercise is False
    assert any(r.code == EligibilityReasonCode.BELOW_MIN_VESTED_THRESHOLD for r in result.reasons)


def test_ineligible_when_no_vested_shares_and_rule_not_enforced():
    settings = _settings(enforce_min_vested_to_exercise=False)
    membership = _membership()
    totals = _totals(vested=0, unvested=100)
    result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=settings,
        totals=totals,
        as_of_date=date.today(),
    )
    assert result.eligible_to_exercise is False
    assert any(r.code == EligibilityReasonCode.NO_VESTED_SHARES for r in result.reasons)
