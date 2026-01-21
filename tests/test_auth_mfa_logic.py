from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.api import deps
from app.core import security
from app.core.security import get_password_hash
from app.core.settings import settings
from app.db.session import get_db
from app.main import app


# Reuse DummyUser, FakeSession, etc.
class DummyUser:
    def __init__(self) -> None:
        self.id = uuid4()
        self.org_id = "default"
        self.email = "user@example.com"
        self.hashed_password = get_password_hash("Password123!")
        self.is_active = True
        self.is_superuser = False
        self.mfa_enabled = False
        self.token_version = 0
        self.last_active_at = None


class FakeResult:
    def __init__(self, user):
        self.user = user

    def scalar_one_or_none(self):
        return self.user


class FakeSession:
    def __init__(self, user: DummyUser) -> None:
        self.user = user
        self.added = []
        self.committed = False

    def add(self, obj) -> None:
        self.added.append(obj)

    async def execute(self, stmt):
        return FakeResult(self.user)

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


@pytest.mark.asyncio
async def test_mfa_required_logic(monkeypatch, tmp_path):
    _patch_keys(monkeypatch, tmp_path)

    # Mock services
    async def noop_enforce(ip, email):
        return None

    async def noop_record(email, success):
        return None

    monkeypatch.setattr("app.api.v1.routers.auth.enforce_login_limits", noop_enforce)
    monkeypatch.setattr("app.api.v1.routers.auth.record_login_attempt", noop_record)

    # Define scenarios
    scenarios = [
        # (org_require_mfa, user_mfa_enabled, user_has_sensitive_perms, expected_mfa_required, expected_setup_required)
        # 1. Base case: No MFA required, no sensitive perms -> No MFA
        (False, False, False, False, False),
        # 2. Org requires MFA, user not enabled -> Setup required
        (True, False, False, True, True),
        # 3. Org requires MFA, user enabled -> MFA required
        (True, True, False, True, False),
        # 4. User has sensitive perms, user not enabled -> Setup required (Enforce MFA)
        (False, False, True, True, True),
        # 5. User has sensitive perms, user enabled -> MFA required
        (False, True, True, True, False),
        # 6. User enabled MFA voluntary, org doesn't require -> MFA required (Fix for original bug)
        (False, True, False, True, False),
    ]

    for i, (org_req, user_mfa, has_sensitive, exp_mfa, exp_setup) in enumerate(scenarios):
        user = DummyUser()
        user.mfa_enabled = user_mfa
        session = FakeSession(user)
        override_dependencies(user, session)

        # Mock settings
        async def mock_get_settings(*args):
            return DummyOrgSettings(require_two_factor=org_req)

        monkeypatch.setattr(
            "app.api.v1.routers.auth.settings_service.get_org_settings", mock_get_settings
        )

        # Mock authz
        async def mock_has_sensitive(*args):
            return has_sensitive

        monkeypatch.setattr(
            "app.api.v1.routers.auth.authz_service.has_sensitive_permissions", mock_has_sensitive
        )

        client = TestClient(app)

        # Get login challenge first
        start_resp = client.post("/api/v1/auth/login/start", json={"email": user.email})
        resp_json = start_resp.json()
        if "data" in resp_json:
            challenge = resp_json["data"]["challenge_token"]
        else:
            challenge = resp_json["challenge_token"]

        complete_resp = client.post(
            "/api/v1/auth/login/complete",
            json={"challenge_token": challenge, "password": "Password123!"},
        )
        assert complete_resp.status_code == 200
        resp_json = complete_resp.json()
        if "data" in resp_json:
            data = resp_json["data"]
        else:
            data = resp_json

        if exp_setup:
            assert (
                data.get("mfa_setup_required") is True
            ), f"Failed for org={org_req}, user_mfa={user_mfa}, sensitive={has_sensitive}"
        elif exp_mfa:
            assert (
                data.get("mfa_required") is True
            ), f"Failed for org={org_req}, user_mfa={user_mfa}, sensitive={has_sensitive}"
        else:
            assert (
                "access_token" in data
            ), f"Failed for org={org_req}, user_mfa={user_mfa}, sensitive={has_sensitive}"
            assert data.get("mfa_required") is not True

        clear_overrides()
