from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import deps
from app.core.errors import register_exception_handlers
from app.core.response_envelope import register_response_envelope


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    register_response_envelope(app)

    @app.get("/protected")
    async def protected_route(user=Depends(deps.require_authenticated_user)):
        return {"user": str(user)}

    return app


def test_protected_route_requires_auth(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_protected_route_allows_authenticated(monkeypatch):
    app = _build_app()

    async def fake_user():
        return {"id": "user-1"}

    app.dependency_overrides[deps.get_current_user] = fake_user
    client = TestClient(app)
    resp = client.get("/protected", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    assert resp.json()["data"]["user"] == "{'id': 'user-1'}"

    app.dependency_overrides.clear()
