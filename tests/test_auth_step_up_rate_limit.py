from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from conftest import FakeAsyncSession, FakeResult, make_identity, make_user

from app.api import deps
from app.core import security
from app.db.session import get_db
from app.main import app
from app.services import mfa as mfa_service


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_step_up_rate_limit(patch_jwt_keys, monkeypatch):
    identity = make_identity(
        mfa_enabled=True,
        mfa_secret_encrypted=mfa_service.encrypt_secret("JBSWY3DPEHPK3PXP"),
    )
    user = make_user(identity=identity)
    db = FakeAsyncSession()
    db.on_execute_return(FakeResult(scalar=identity))

    async def _get_current_user():
        return user

    async def _get_db():
        yield db

    async def _get_ctx():
        return deps.TenantContext(org_id="default")

    app.dependency_overrides[deps.get_current_user] = _get_current_user
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx

    # Mock services
    def noop_verify_totp(secret, code):
        return False  # Always fail to test rate limit

    monkeypatch.setattr("app.services.mfa.verify_totp", noop_verify_totp)

    mock_limit = AsyncMock()
    monkeypatch.setattr("app.api.v1.routers.auth.enforce_mfa_rate_limit", mock_limit)

    client = TestClient(app)

    challenge_token = security.create_step_up_challenge_token(
        str(user.id), user.org_id, "TEST_ACTION"
    )

    resp = client.post(
        "/api/v1/auth/step-up/verify", json={"challenge_token": challenge_token, "code": "000000"}
    )

    # In the fixed code, this should be 401 (invalid code) AND mock_limit SHOULD have been called.
    assert resp.status_code == 401
    assert mock_limit.call_count == 1
