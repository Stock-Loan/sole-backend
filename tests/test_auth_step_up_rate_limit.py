from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.api import deps
from app.core import security
from app.core.security import get_password_hash
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services import mfa as mfa_service

# Reuse DummyUser, FakeSession, etc.
class DummyUser:
    def __init__(self) -> None:
        self.id = uuid4()
        self.org_id = "default"
        self.email = "user@example.com"
        self.hashed_password = get_password_hash("Password123!")
        self.is_active = True
        self.is_superuser = False
        self.mfa_enabled = True
        self.token_version = 0
        self.last_active_at = None
        self.mfa_secret_encrypted = mfa_service.encrypt_secret("JBSWY3DPEHPK3PXP") # Base32 secret

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

@pytest.mark.asyncio
async def test_step_up_rate_limit(monkeypatch, tmp_path):
    _patch_keys(monkeypatch, tmp_path)
    
    user = DummyUser()
    session = FakeSession(user)
    override_dependencies(user, session)
    
    # Mock services
    def noop_verify_totp(secret, code):
        return False # Always fail to test rate limit
    monkeypatch.setattr("app.services.mfa.verify_totp", noop_verify_totp)

    # Use a real redis mock or patch the enforce_mfa_rate_limit to track calls
    # For this reproduction, we'll patch enforce_mfa_rate_limit to verify it's called
    # But wait, we want to prove it's NOT called. 
    # So we'll patch it to raise an exception if called, and if the test passes without exception, it confirms it wasn't called.
    
    async def mock_rate_limit(token):
        raise RuntimeError("Rate limit check was called!")
    
    # We want to assert that this is NOT called in the current code
    # But wait, if we want to assert checking rate limit, we need to know if it's there.
    # The vulnerability says it's MISSING.
    # So if we run the code and it doesn't call mock_rate_limit, we've reproduced the issue.
    # However, to be more robust, let's use a mock side effect.
    
    from unittest.mock import AsyncMock
    mock_limit = AsyncMock()
    # Patch where it is used, not where it is defined, because it is imported directly
    monkeypatch.setattr("app.api.v1.routers.auth.enforce_mfa_rate_limit", mock_limit)

    client = TestClient(app)
    
    challenge_token = security.create_step_up_challenge_token(str(user.id), user.org_id, "TEST_ACTION")
    
    resp = client.post(
        "/api/v1/auth/step-up/verify",
        json={"challenge_token": challenge_token, "code": "000000"}
    )
    
    # In the fixed code, this should be 401 (invalid code) AND mock_limit SHOULD have been called.
    assert resp.status_code == 401
    assert mock_limit.call_count == 1

