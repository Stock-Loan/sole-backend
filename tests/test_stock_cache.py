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
from app.services import stock_dashboard, stock_summary


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value


def _membership() -> OrgMembership:
    return OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=uuid4(),
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
        employment_start_date=date.today() - timedelta(days=365),
    )


def _settings() -> OrgSettings:
    return OrgSettings(
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


def _grant(membership_id) -> EmployeeStockGrant:
    grant = EmployeeStockGrant(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership_id,
        grant_date=date(2025, 1, 1),
        total_shares=100,
        exercise_price=Decimal("1.25"),
        status="ACTIVE",
        vesting_strategy="SCHEDULED",
    )
    grant.vesting_events = [
        VestingEvent(
            id=uuid4(),
            org_id="default",
            grant_id=grant.id,
            vest_date=date(2025, 6, 1),
            shares=100,
        )
    ]
    return grant


@pytest.mark.asyncio
async def test_stock_summary_cache_round_trip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(stock_summary, "get_redis_client", lambda: fake)

    membership = _membership()
    settings = _settings()
    grant = _grant(membership.id)
    summary = stock_summary.build_stock_summary_from_data(
        membership=membership,
        org_settings=settings,
        grants=[grant],
        as_of_date=date(2025, 7, 1),
    )
    await stock_summary._set_cached_summary(summary, "default", membership.id, date(2025, 7, 1))
    cached = await stock_summary._get_cached_summary("default", membership.id, date(2025, 7, 1))
    assert cached is not None
    assert cached.model_dump() == summary.model_dump()


@pytest.mark.asyncio
async def test_stock_dashboard_cache_round_trip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(stock_dashboard, "get_redis_client", lambda: fake)

    member = _membership()
    settings = _settings()
    grant = _grant(member.id)
    summary = stock_dashboard.build_dashboard_summary_from_data(
        org_id="default",
        memberships=[member],
        org_settings=settings,
        grants=[grant],
        as_of_date=date(2025, 7, 1),
    )
    await stock_dashboard._set_cached_summary(summary, "default", date(2025, 7, 1))
    cached = await stock_dashboard._get_cached_summary("default", date(2025, 7, 1))
    assert cached is not None
    assert cached.model_dump() == summary.model_dump()
