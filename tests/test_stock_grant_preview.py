import os
from datetime import date
from decimal import Decimal

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.schemas.stock import EmployeeStockGrantCreate
from app.services import stock_grants


def test_preview_immediate_grant_generates_event():
    payload = EmployeeStockGrantCreate(
        grant_date=date(2025, 1, 1),
        total_shares=100,
        exercise_price=Decimal("1.25"),
        vesting_strategy="IMMEDIATE",
        vesting_events=[],
    )
    preview = stock_grants.preview_grant(payload, date(2024, 12, 1))
    assert len(preview.vesting_events) == 1
    assert preview.vesting_events[0].vest_date == date(2025, 1, 1)
    assert preview.vesting_events[0].shares == 100
    assert preview.next_vesting_event is not None


def test_preview_rejects_invalid_schedule():
    payload = EmployeeStockGrantCreate(
        grant_date=date(2025, 1, 1),
        total_shares=100,
        exercise_price=Decimal("1.25"),
        vesting_strategy="SCHEDULED",
        vesting_events=[],
    )
    with pytest.raises(ValueError, match="requires vesting_events"):
        stock_grants.preview_grant(payload, date(2025, 1, 1))
