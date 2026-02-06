import os
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.models.vesting_event import VestingEvent
from app.services import stock_dashboard


def _membership(start_days_ago: int, *, status: str = "ACTIVE") -> OrgMembership:
    return OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=uuid4(),
        employee_id=str(uuid4())[:8],
        employment_status=status,
        platform_status=status,
        employment_start_date=date.today() - timedelta(days=start_days_ago),
    )


def _settings(**overrides) -> OrgSettings:
    defaults = dict(
        org_id="default",
        allow_user_data_export=True,
        allow_profile_edit=True,
        require_two_factor=False,
        audit_log_retention_days=180,
        inactive_user_retention_days=180,
        enforce_service_duration_rule=True,
        min_service_duration_years=0.5,
        enforce_min_vested_to_exercise=True,
        min_vested_shares_to_exercise=100,
    )
    defaults.update(overrides)
    return OrgSettings(**defaults)


def _grant(membership_id, grant_date: date, total_shares: int) -> EmployeeStockGrant:
    grant = EmployeeStockGrant(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership_id,
        grant_date=grant_date,
        total_shares=total_shares,
        exercise_price=Decimal("1.00"),
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


def test_dashboard_summary_counts():
    as_of = date(2025, 7, 1)
    settings = _settings()

    member_ok = _membership(365)
    member_service_short = _membership(30)
    member_inactive = _membership(365, status="SUSPENDED")
    member_below_min = _membership(365)

    grant_ok = _grant(member_ok.id, date(2025, 1, 1), 200)
    grant_ok.vesting_events = [
        _event(grant_ok, date(2025, 6, 1), 100),
        _event(grant_ok, date(2025, 12, 1), 100),
    ]

    grant_service_short = _grant(member_service_short.id, date(2025, 1, 1), 150)
    grant_service_short.vesting_events = [_event(grant_service_short, date(2025, 6, 1), 150)]

    grant_inactive = _grant(member_inactive.id, date(2025, 1, 1), 50)
    grant_inactive.vesting_events = [_event(grant_inactive, date(2025, 6, 1), 50)]

    grant_below_min = _grant(member_below_min.id, date(2025, 1, 1), 80)
    grant_below_min.vesting_events = [_event(grant_below_min, date(2025, 6, 1), 80)]

    summary = stock_dashboard.build_dashboard_summary_from_data(
        org_id="default",
        memberships=[member_ok, member_service_short, member_inactive, member_below_min],
        org_settings=settings,
        grants=[grant_ok, grant_service_short, grant_inactive, grant_below_min],
        as_of_date=as_of,
    )

    assert summary.total_program_employees == 4
    assert summary.total_granted_shares == 480
    assert summary.total_vested_shares == 380
    assert summary.total_unvested_shares == 100
    assert summary.eligible_to_exercise_count == 1
    assert summary.not_eligible_due_to_service_count == 1
    assert summary.not_eligible_due_to_min_vested_count == 1
    assert summary.not_eligible_due_to_other_count == 1
    assert summary.next_global_vesting_date == date(2025, 12, 1)
