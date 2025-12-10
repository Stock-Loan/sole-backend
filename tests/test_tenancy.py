from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import deps
from app.core.settings import settings

@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://test:test@localhost:5432/test")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "secret_key", "test-secret-key-boot")
    monkeypatch.setattr(settings, "default_org_id", "default")
    yield


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ctx")
    async def ctx_route(ctx: deps.TenantContext = Depends(deps.get_tenant_context)):
        return {"org_id": ctx.org_id}

    return app


def test_single_mode_uses_default_org(monkeypatch):
    monkeypatch.setattr(settings, "tenancy_mode", "single")
    monkeypatch.setattr(settings, "default_org_id", "single-org")
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/ctx")
    assert resp.status_code == 200
    assert resp.json()["org_id"] == "single-org"


def test_multi_mode_requires_header_or_subdomain(monkeypatch):
    monkeypatch.setattr(settings, "tenancy_mode", "multi")
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/ctx")
    assert resp.status_code == 400
    assert "Tenant resolution failed" in resp.json()["detail"]


def test_multi_mode_accepts_header(monkeypatch):
    monkeypatch.setattr(settings, "tenancy_mode", "multi")
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/ctx", headers={"X-Tenant-ID": "org-123"})
    assert resp.status_code == 200
    assert resp.json()["org_id"] == "org-123"


def test_multi_mode_accepts_subdomain(monkeypatch):
    monkeypatch.setattr(settings, "tenancy_mode", "multi")
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/ctx", headers={"host": "acme.example.com"})
    assert resp.status_code == 200
    assert resp.json()["org_id"] == "acme"
