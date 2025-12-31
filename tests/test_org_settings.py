import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.db.session import get_db
from app.main import app
from app.models.org_settings import OrgSettings
from app.services import authz
from app.models.audit_log import AuditLog
from app.schemas.settings import OrgSettingsUpdate
from app.services import settings as settings_service


class DummyUser:
    def __init__(self) -> None:
        self.id = uuid4()
        self.org_id = "default"
        self.is_active = True
        self.is_superuser = False


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self) -> None:
        self.settings_obj: OrgSettings | None = None
        self.added = []

    async def execute(self, stmt):
        return FakeResult(self.settings_obj)

    def add(self, obj) -> None:
        self.added.append(obj)
        if isinstance(obj, OrgSettings):
            self.settings_obj = obj

    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:
        return None


def override_dependencies(session: FakeSession) -> None:
    async def _get_ctx():
        return deps.TenantContext(org_id="default")

    async def _get_db():
        yield session

    async def _require_user():
        return DummyUser()

    app.dependency_overrides[deps.get_tenant_context] = _get_ctx
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.require_authenticated_user] = _require_user


def clear_overrides() -> None:
    app.dependency_overrides.pop(deps.get_tenant_context, None)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(deps.require_authenticated_user, None)


@pytest.fixture(autouse=True)
def _reset_overrides(monkeypatch):
    async def _allow(*args, **kwargs):
        return True

    monkeypatch.setattr(authz, "check_permission", _allow)
    yield
    clear_overrides()


def test_org_settings_defaults_include_stock_rules():
    session = FakeSession()
    override_dependencies(session)
    client = TestClient(app)

    resp = client.get("/api/v1/org/settings")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enforce_service_duration_rule"] is False
    assert data["min_service_duration_days"] is None
    assert data["enforce_min_vested_to_exercise"] is False
    assert data["min_vested_shares_to_exercise"] is None


def test_org_settings_validation_rejects_inconsistent_rules():
    session = FakeSession()
    override_dependencies(session)
    client = TestClient(app)

    resp = client.put(
        "/api/v1/org/settings",
        json={
            "enforce_service_duration_rule": False,
            "min_service_duration_days": 10,
        },
    )
    assert resp.status_code == 400
    assert "min_service_duration_days must be null" in resp.json()["message"]


def test_org_settings_update_persists_stock_rules():
    session = FakeSession()
    override_dependencies(session)
    client = TestClient(app)

    update_resp = client.put(
        "/api/v1/org/settings",
        json={
            "enforce_service_duration_rule": True,
            "min_service_duration_days": 180,
            "enforce_min_vested_to_exercise": True,
            "min_vested_shares_to_exercise": 1000,
        },
    )
    assert update_resp.status_code == 200
    update_data = update_resp.json()["data"]
    assert update_data["enforce_service_duration_rule"] is True
    assert update_data["min_service_duration_days"] == 180
    assert update_data["enforce_min_vested_to_exercise"] is True
    assert update_data["min_vested_shares_to_exercise"] == 1000

    get_resp = client.get("/api/v1/org/settings")
    assert get_resp.status_code == 200
    get_data = get_resp.json()["data"]
    assert get_data["min_service_duration_days"] == 180
    assert get_data["min_vested_shares_to_exercise"] == 1000


@pytest.mark.asyncio
async def test_org_settings_update_writes_audit_log():
    session = FakeSession()
    ctx = deps.TenantContext(org_id="default")
    session.settings_obj = OrgSettings(org_id="default")
    payload = OrgSettingsUpdate(
        allow_profile_edit=False,
    )
    await settings_service.update_org_settings(
        session,
        ctx,
        payload,
        actor_id="actor-1",
    )
    assert any(isinstance(obj, AuditLog) for obj in session.added)
