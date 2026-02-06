import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.vesting_event import VestingEvent
from app.services import vesting_engine


def _grant(*, grant_date: date, total_shares: int, strategy: str) -> EmployeeStockGrant:
    grant = EmployeeStockGrant(
        id=uuid4(),
        org_id="default",
        org_membership_id=uuid4(),
        grant_date=grant_date,
        total_shares=total_shares,
        exercise_price=Decimal("1.25"),
        status="ACTIVE",
        vesting_strategy=strategy,
    )
    grant.vesting_events = []
    return grant


def _event(grant_id, vest_date: date, shares: int) -> VestingEvent:
    return VestingEvent(
        id=uuid4(),
        org_id="default",
        grant_id=grant_id,
        vest_date=vest_date,
        shares=shares,
    )


def test_immediate_full_vest():
    grant = _grant(grant_date=date(2025, 1, 1), total_shares=100, strategy="IMMEDIATE")
    vested, unvested = vesting_engine.compute_grant_vesting(grant, date(2025, 2, 1))
    assert vested == 100
    assert unvested == 0


def test_scheduled_partial_vest():
    grant = _grant(grant_date=date(2025, 1, 1), total_shares=100, strategy="SCHEDULED")
    grant.vesting_events = [
        _event(grant.id, date(2025, 6, 1), 40),
        _event(grant.id, date(2026, 1, 1), 60),
    ]
    vested, unvested = vesting_engine.compute_grant_vesting(grant, date(2025, 7, 1))
    assert vested == 40
    assert unvested == 60


def test_no_events_vests_zero():
    grant = _grant(grant_date=date(2025, 1, 1), total_shares=100, strategy="SCHEDULED")
    vested, unvested = vesting_engine.compute_grant_vesting(grant, date(2025, 2, 1))
    assert vested == 0
    assert unvested == 100


def test_next_vesting_event_across_grants():
    grant_one = _grant(grant_date=date(2025, 1, 1), total_shares=100, strategy="SCHEDULED")
    grant_one.vesting_events = [
        _event(grant_one.id, date(2025, 6, 1), 50),
        _event(grant_one.id, date(2025, 7, 1), 50),
    ]
    grant_two = _grant(grant_date=date(2025, 5, 15), total_shares=200, strategy="IMMEDIATE")
    grants = [grant_one, grant_two]
    next_event = vesting_engine.next_vesting_event(grants, date(2025, 5, 1))
    assert next_event is not None
    assert next_event.vest_date == date(2025, 5, 15)
    assert next_event.shares == 200
