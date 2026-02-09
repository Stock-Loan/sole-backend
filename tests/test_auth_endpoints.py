import pytest
from fastapi.testclient import TestClient

from conftest import FakeAsyncSession, FakeResult, make_identity, make_user

from app.api import deps
from app.core.security import verify_password
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _override(user, session):
    async def _get_current_user():
        return user

    async def _get_current_user_allow_password_change():
        return user

    async def _get_db():
        yield session

    async def _get_ctx():
        return deps.TenantContext(org_id="default")

    app.dependency_overrides[deps.get_current_user] = _get_current_user
    app.dependency_overrides[deps.get_current_user_allow_password_change] = (
        _get_current_user_allow_password_change
    )
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx


def get_data(resp):
    json_data = resp.json()
    if "data" in json_data:
        return json_data["data"]
    return json_data


def test_change_password_success(patch_jwt_keys):
    identity = make_identity(password="OldPassword123!")
    user = make_user(identity=identity)
    db = FakeAsyncSession()
    db.on_execute_return(FakeResult(scalar=identity))
    _override(user, db)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "OldPassword123!", "new_password": "NewPassword123!"},
    )
    assert resp.status_code == 200
    data = get_data(resp)
    assert "access_token" in data and "refresh_token" in data
    assert identity.token_version == 1
    assert verify_password("NewPassword123!", identity.hashed_password)
    assert not verify_password("OldPassword123!", identity.hashed_password)
    assert db.committed is True


def test_change_password_rejects_wrong_current(patch_jwt_keys):
    identity = make_identity(password="OldPassword123!")
    user = make_user(identity=identity)
    db = FakeAsyncSession()
    db.on_execute_return(FakeResult(scalar=identity))
    _override(user, db)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "bad-password", "new_password": "AnotherPassword123!"},
    )
    assert resp.status_code == 400
    assert "incorrect" in resp.json()["message"]
    assert verify_password("OldPassword123!", identity.hashed_password)
    assert db.committed is False


def test_login_returns_pre_org_token(monkeypatch, patch_jwt_keys):
    identity = make_identity(password="OldPassword123!")
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
        json={"email": identity.email, "password": "OldPassword123!"},
    )
    assert resp.status_code == 200
    data = get_data(resp)
    assert "pre_org_token" in data
    assert data.get("must_change_password") is False
