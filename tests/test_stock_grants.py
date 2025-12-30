import os
from datetime import date
from uuid import uuid4

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.models.org_membership import OrgMembership
from app.schemas.stock import VestingEventCreate, VestingStrategy
from app.services.stock_grants import _build_vesting_events, _ensure_membership_active


def test_immediate_vesting_builds_single_event():
    grant_date = date(2025, 1, 1)
    events = _build_vesting_events(VestingStrategy.IMMEDIATE, grant_date, 100, [])
    assert len(events) == 1
    assert events[0].vest_date == grant_date
    assert events[0].shares == 100


def test_scheduled_vesting_allows_multi_step_schedule():
    grant_date = date(2025, 1, 1)
    schedule = [
        VestingEventCreate(vest_date=date(2025, 6, 1), shares=40),
        VestingEventCreate(vest_date=date(2026, 1, 1), shares=60),
    ]
    events = _build_vesting_events(VestingStrategy.SCHEDULED, grant_date, 100, schedule)
    assert len(events) == 2
    assert sum(e.shares for e in events) == 100


def test_vesting_schedule_rejects_over_allocation():
    grant_date = date(2025, 1, 1)
    schedule = [
        VestingEventCreate(vest_date=date(2025, 6, 1), shares=60),
        VestingEventCreate(vest_date=date(2026, 1, 1), shares=60),
    ]
    with pytest.raises(ValueError, match="cannot exceed total_shares"):
        _build_vesting_events(VestingStrategy.SCHEDULED, grant_date, 100, schedule)


def test_inactive_membership_cannot_receive_grants():
    membership = OrgMembership(
        org_id="default",
        user_id=uuid4(),
        employee_id="E-001",
        employment_status="ACTIVE",
        platform_status="SUSPENDED",
    )
    with pytest.raises(ValueError, match="Membership must be ACTIVE"):
        _ensure_membership_active(membership)
