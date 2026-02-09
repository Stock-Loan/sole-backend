import pytest
from fastapi.testclient import TestClient

from app.core import health as health_module
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    # Keep required secrets/settings present for health calls during tests
    monkeypatch.setattr(health_module.settings, "environment", "test")
    yield


def test_health_live_returns_ok() -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload.get("status") == "ok"
    assert "timestamp" in payload


def test_health_ready_ok(monkeypatch) -> None:
    async def ok_db():
        return {"status": "ok"}

    async def ok_redis():
        return {"status": "ok"}

    monkeypatch.setattr(health_module, "_check_db", ok_db)
    monkeypatch.setattr(health_module, "_check_redis", ok_redis)

    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload.get("status") == "ok"
    assert payload.get("ready") is True
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["redis"]["status"] == "ok"


def test_health_ready_degraded(monkeypatch) -> None:
    async def bad_db():
        return {"status": "error", "error": "unreachable"}

    async def ok_redis():
        return {"status": "ok"}

    monkeypatch.setattr(health_module, "_check_db", bad_db)
    monkeypatch.setattr(health_module, "_check_redis", ok_redis)

    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload.get("status") == "degraded"
    assert payload.get("ready") is False
    assert payload["checks"]["database"]["status"] == "error"


def test_status_summary(monkeypatch) -> None:
    async def ok_db():
        return {"status": "ok"}

    async def ok_redis():
        return {"status": "ok"}

    monkeypatch.setattr(health_module, "_check_db", ok_db)
    monkeypatch.setattr(health_module, "_check_redis", ok_redis)

    response = client.get("/api/v1/status/summary")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload.get("status") == "ok"
    assert payload.get("ready") is True
    assert payload.get("version") == health_module.APP_VERSION
