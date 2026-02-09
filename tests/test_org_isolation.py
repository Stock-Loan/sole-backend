from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from conftest import FakeAsyncSession, FakeResult

from app.api import deps
from app.core.errors import register_exception_handlers
from app.core.response_envelope import register_response_envelope
from app.core.settings import settings


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    register_response_envelope(app)

    @app.get("/ctx")
    async def ctx_route(ctx: deps.TenantContext = Depends(deps.get_tenant_context)):
        return {"org_id": ctx.org_id}

    return app


def _override_db(app: FastAPI, org_exists: bool = True):
    db = FakeAsyncSession()
    db.on_execute_return(FakeResult(scalar="ok" if org_exists else None))

    async def _fake_db():
        return db

    app.dependency_overrides[deps.get_db_session] = _fake_db


def test_multi_tenant_rejects_header_token_mismatch(monkeypatch):
    monkeypatch.setattr(settings, "tenancy_mode", "multi")
    monkeypatch.setattr(
        deps,
        "decode_token",
        lambda _token: {"org": "org-a", "sub": "user-1", "su": False},
    )

    app = _build_app()
    _override_db(app, org_exists=True)
    client = TestClient(app)

    resp = client.get(
        "/ctx",
        headers={"Authorization": "Bearer test-token", "X-Org-Id": "org-b"},
    )
    assert resp.status_code == 403
    assert "Tenant header does not match token" in resp.json()["message"]


def test_multi_tenant_requires_membership(monkeypatch):
    monkeypatch.setattr(settings, "tenancy_mode", "multi")
    monkeypatch.setattr(
        deps,
        "decode_token",
        lambda _token: {"org": "org-a", "sub": "user-1", "su": False},
    )

    async def _no_membership(*_args, **_kwargs):
        return None

    monkeypatch.setattr(deps, "get_membership", _no_membership)

    app = _build_app()
    _override_db(app, org_exists=True)
    client = TestClient(app)

    resp = client.get(
        "/ctx",
        headers={"Authorization": "Bearer test-token", "X-Org-Id": "org-a"},
    )
    assert resp.status_code == 403
    assert "User is not a member of this organization" in resp.json()["message"]


def test_superuser_can_override_header_org(monkeypatch):
    monkeypatch.setattr(settings, "tenancy_mode", "multi")
    monkeypatch.setattr(
        deps,
        "decode_token",
        lambda _token: {"org": "org-a", "sub": "user-1", "su": True},
    )

    app = _build_app()
    _override_db(app, org_exists=True)
    client = TestClient(app)

    resp = client.get(
        "/ctx",
        headers={"Authorization": "Bearer test-token", "X-Org-Id": "org-b"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["org_id"] == "org-b"
