from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import deps


def _build_app() -> FastAPI:
    app = FastAPI()

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
    assert resp.json()["user"] == "{'id': 'user-1'}"

    app.dependency_overrides.clear()
