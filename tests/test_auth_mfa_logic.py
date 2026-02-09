import pytest
from fastapi.testclient import TestClient

from conftest import FakeAsyncSession, FakeResult, make_identity, make_user, make_org_settings

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _override(user, session):
    async def _get_current_user():
        return user

    async def _get_db():
        yield session

    async def _get_ctx():
        return deps.TenantContext(org_id="default")

    app.dependency_overrides[deps.get_current_user] = _get_current_user
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx


def test_login_requires_credentials(monkeypatch, patch_jwt_keys):
    """Login endpoint rejects missing or invalid credentials."""
    identity = make_identity(password="Password123!")
    user = make_user(identity=identity)
    db = FakeAsyncSession()
    db.on_execute_return(FakeResult(scalar=identity))
    _override(user, db)

    async def noop_enforce(ip, email):
        return None

    async def noop_record(email, success):
        return None

    monkeypatch.setattr("app.api.v1.routers.auth.enforce_login_limits", noop_enforce)
    monkeypatch.setattr("app.api.v1.routers.auth.record_login_attempt", noop_record)

    client = TestClient(app)

    # Wrong password
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": identity.email, "password": "WrongPassword!"},
    )
    assert resp.status_code == 401

    # Correct password
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": identity.email, "password": "Password123!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    if "data" in data:
        data = data["data"]
    assert "pre_org_token" in data


def test_login_reports_must_change_password(monkeypatch, patch_jwt_keys):
    """Login response includes must_change_password when identity requires it."""
    identity = make_identity(password="Password123!", must_change_password=True)
    user = make_user(identity=identity)
    db = FakeAsyncSession()
    db.on_execute_return(FakeResult(scalar=identity))
    _override(user, db)

    async def noop_enforce(ip, email):
        return None

    async def noop_record(email, success):
        return None

    monkeypatch.setattr("app.api.v1.routers.auth.enforce_login_limits", noop_enforce)
    monkeypatch.setattr("app.api.v1.routers.auth.record_login_attempt", noop_record)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": identity.email, "password": "Password123!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    if "data" in data:
        data = data["data"]
    assert data["must_change_password"] is True


def test_orgs_endpoint_requires_auth(patch_jwt_keys):
    """GET /auth/orgs returns 401 without a valid token."""
    client = TestClient(app)
    resp = client.get("/api/v1/auth/orgs")
    assert resp.status_code in (401, 403)
