from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.api import deps
from app.core import security
from app.core.security import get_password_hash
from app.core.settings import settings
from app.db.session import get_db
from app.main import app


class DummyIdentity:
    def __init__(self, mfa_enabled=False) -> None:
        self.id = uuid4()
        self.email = "user@example.com"
        self.hashed_password = get_password_hash("Password123!")
        self.is_active = True
        self.mfa_enabled = mfa_enabled
        self.mfa_method = "totp" if mfa_enabled else None
        self.mfa_secret_encrypted = "encrypted-secret" if mfa_enabled else None
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


class FakeSession:
    def __init__(self, *, identity: DummyIdentity, user: DummyUser) -> None:
        self.identity = identity
        self.user = user
        self.added = []
        self.committed = False

    def add(self, obj) -> None:
        self.added.append(obj)

    async def execute(self, stmt):
        return FakeResult(self.identity)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, obj) -> None:
        return None


def override_dependencies(user: DummyUser, session: FakeSession) -> None:
    async def _get_current_user():
        return user

    async def _get_db():
        yield session

    async def _get_ctx():
        return deps.TenantContext(org_id="default")

    app.dependency_overrides[deps.get_current_user] = _get_current_user
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx


def clear_overrides() -> None:
    app.dependency_overrides.pop(deps.get_current_user, None)
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
    monkeypatch.setattr(settings, "secret_key", "placeholder-secret")
    security._load_private_key.cache_clear()
    security._load_public_key.cache_clear()


class DummyOrgSettings:
    def __init__(self, require_two_factor=False):
        self.require_two_factor = require_two_factor
        self.remember_device_days = 30
        self.mfa_required_actions = []


def test_login_requires_credentials(monkeypatch, tmp_path):
    """Login endpoint rejects missing or invalid credentials."""
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


def test_login_reports_must_change_password(monkeypatch, tmp_path):
    """Login response includes must_change_password when identity requires it."""
    identity = DummyIdentity()
    identity.must_change_password = True
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
        json={"email": identity.email, "password": "Password123!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    if "data" in data:
        data = data["data"]
    assert data["must_change_password"] is True


def test_orgs_endpoint_requires_auth(monkeypatch, tmp_path):
    """GET /auth/orgs returns 401 without a valid token."""
    _patch_keys(monkeypatch, tmp_path)

    client = TestClient(app)
    resp = client.get("/api/v1/auth/orgs")
    assert resp.status_code in (401, 403)
