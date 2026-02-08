from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core import security
from app.core.security import get_password_hash, verify_password
from app.core.settings import settings
from app.db.session import get_db
from app.main import app


class DummyIdentity:
    def __init__(self) -> None:
        self.id = uuid4()
        self.email = "user@example.com"
        self.hashed_password = get_password_hash("OldPassword123!")
        self.is_active = True
        self.mfa_enabled = False
        self.mfa_method = None
        self.mfa_secret_encrypted = None
        self.mfa_confirmed_at = None
        self.token_version = 0
        self.last_active_at = None
        self.must_change_password = False


class DummyUser:
    def __init__(self, identity: DummyIdentity) -> None:
        self.id = uuid4()
        self.org_id = "default"
        self.identity_id = identity.id
        self.identity = identity
        self.email = identity.email
        self.is_active = True
        self.is_superuser = False


class FakeResult:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj

    def all(self):
        return []


class FakeSession:
    def __init__(self, *, identity: DummyIdentity, user: DummyUser) -> None:
        self.identity = identity
        self.user = user
        self.added = []
        self.committed = False

    def add(self, obj) -> None:
        self.added.append(obj)

    async def execute(self, stmt):
        # Return identity or user based on stmt context
        return FakeResult(self.identity)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj) -> None:
        return None


def override_dependencies(user: DummyUser, session: FakeSession) -> None:
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


def clear_overrides() -> None:
    app.dependency_overrides.pop(deps.get_current_user, None)
    app.dependency_overrides.pop(deps.get_current_user_allow_password_change, None)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(deps.get_tenant_context, None)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    clear_overrides()


def _patch_keys(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_file = tmp_path / "priv.pem"
    pub_file = tmp_path / "pub.pem"
    priv_file.write_bytes(private_pem)
    pub_file.write_bytes(public_pem)
    monkeypatch.setattr(settings, "jwt_private_key_path", str(priv_file))
    monkeypatch.setattr(settings, "jwt_public_key_path", str(pub_file))
    monkeypatch.setattr(settings, "secret_key", "placeholder-secret-for-settings")
    security._load_private_key.cache_clear()
    security._load_public_key.cache_clear()


def get_data(resp):
    json_data = resp.json()
    if "data" in json_data:
        return json_data["data"]
    return json_data


def test_change_password_success(tmp_path, monkeypatch):
    identity = DummyIdentity()
    user = DummyUser(identity)
    session = FakeSession(identity=identity, user=user)
    override_dependencies(user, session)
    _patch_keys(monkeypatch, tmp_path)

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
    assert session.committed is True


def test_change_password_rejects_wrong_current(tmp_path, monkeypatch):
    identity = DummyIdentity()
    user = DummyUser(identity)
    session = FakeSession(identity=identity, user=user)
    override_dependencies(user, session)
    _patch_keys(monkeypatch, tmp_path)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "bad-password", "new_password": "AnotherPassword123!"},
    )
    assert resp.status_code == 400
    assert "incorrect" in resp.json()["message"]
    # Ensure we did not change password or commit
    assert verify_password("OldPassword123!", identity.hashed_password)
    assert session.committed is False


def test_login_returns_pre_org_token(monkeypatch, tmp_path):
    identity = DummyIdentity()
    user = DummyUser(identity)
    session = FakeSession(identity=identity, user=user)
    override_dependencies(user, session)
    _patch_keys(monkeypatch, tmp_path)

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
