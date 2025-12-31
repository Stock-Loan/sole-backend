import os
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.api import deps
from app.db.session import get_db
from app.main import app
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.schemas.stock import EligibilityResult, StockSummaryResponse
from app.services import stock_summary, authz


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, membership: OrgMembership | None):
        self.membership = membership

    async def execute(self, stmt):
        return FakeResult(self.membership)


class DummyUser(User):
    def __init__(self) -> None:
        super().__init__(
            org_id="default",
            email="user@example.com",
            full_name="Test User",
            hashed_password="hash",
            is_active=True,
        )
        self.id = uuid4()


def override_dependencies(session: FakeSession, user: DummyUser) -> None:
    async def _get_db():
        yield session

    async def _get_ctx():
        return deps.TenantContext(org_id="default")

    async def _require_user():
        return user

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx
    app.dependency_overrides[deps.require_authenticated_user] = _require_user
    app.dependency_overrides[deps.get_current_user] = _require_user


def clear_overrides():
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(deps.get_tenant_context, None)
    app.dependency_overrides.pop(deps.require_authenticated_user, None)
    app.dependency_overrides.pop(deps.get_current_user, None)


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    async def _allow(*args, **kwargs):
        return True

    monkeypatch.setattr(authz, "check_permission", _allow)
    yield
    clear_overrides()


def test_me_stock_summary_404_when_no_membership(monkeypatch):
    session = FakeSession(None)
    user = DummyUser()
    override_dependencies(session, user)

    client = TestClient(app)
    resp = client.get("/api/v1/me/stock/summary")
    assert resp.status_code == 404


def test_me_stock_summary_returns_summary(monkeypatch):
    user = DummyUser()
    membership = OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=user.id,
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
    )
    session = FakeSession(membership)
    override_dependencies(session, user)

    summary = StockSummaryResponse(
        org_membership_id=membership.id,
        total_granted_shares=0,
        total_vested_shares=0,
        total_unvested_shares=0,
        next_vesting_event=None,
        eligibility_result=EligibilityResult(
            eligible_to_exercise=False,
            total_granted_shares=0,
            total_vested_shares=0,
            total_unvested_shares=0,
            reasons=[],
        ),
        grants=[],
    )

    async def _stub_build(*args, **kwargs):
        return summary

    monkeypatch.setattr(stock_summary, "build_stock_summary", _stub_build)

    client = TestClient(app)
    resp = client.get("/api/v1/me/stock/summary")
    assert resp.status_code == 200
    assert resp.json()["data"]["org_membership_id"] == str(membership.id)
