from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from conftest import FakeAsyncSession, FakeResult, make_org_settings

from app.api import deps
from app.main import app
from app.models.org_settings import OrgSettings
from app.models.audit_log import AuditLog
from app.schemas.settings import OrgSettingsUpdate
from app.services import settings as settings_service


@pytest.fixture(autouse=True)
def _allow_all(allow_all_permissions):
    pass


def test_org_settings_defaults_include_stock_rules(client):
    resp = client.get("/api/v1/org/settings")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enforce_service_duration_rule"] is False
    assert data["min_service_duration_years"] is None
    assert data["enforce_min_vested_to_exercise"] is False
    assert data["min_vested_shares_to_exercise"] is None
    assert data["allowed_repayment_methods"] == [
        "INTEREST_ONLY",
        "BALLOON",
        "PRINCIPAL_AND_INTEREST",
    ]
    assert data["min_loan_term_months"] == 6
    assert data["max_loan_term_months"] == 60
    assert data["allowed_interest_types"] == ["FIXED", "VARIABLE"]
    assert data["fixed_interest_rate_annual_percent"] == "0"
    assert data["require_down_payment"] is False
    assert data["down_payment_percent"] is None


def test_org_settings_validation_rejects_inconsistent_rules(client):
    resp = client.put(
        "/api/v1/org/settings",
        json={
            "enforce_service_duration_rule": False,
            "min_service_duration_years": 1,
        },
    )
    assert resp.status_code == 400
    assert "min_service_duration_years must be null" in resp.json()["message"]


def test_org_settings_validation_rejects_invalid_loan_term_bounds(client):
    resp = client.put(
        "/api/v1/org/settings",
        json={"min_loan_term_months": 24, "max_loan_term_months": 12},
    )
    assert resp.status_code == 400
    assert "min_loan_term_months must be <= max_loan_term_months" in resp.json()["message"]


def test_org_settings_validation_rejects_empty_repayment_methods(client):
    resp = client.put(
        "/api/v1/org/settings",
        json={"allowed_repayment_methods": []},
    )
    assert resp.status_code == 400
    assert (
        "allowed_repayment_methods must include at least one repayment method"
        in resp.json()["message"]
    )


def test_org_settings_validation_requires_down_payment_percent(client):
    resp = client.put(
        "/api/v1/org/settings",
        json={"require_down_payment": True},
    )
    assert resp.status_code == 400
    assert "down_payment_percent is required" in resp.json()["message"]


def test_org_settings_update_persists_stock_rules(fake_db, client):
    # Track OrgSettings objects added to the session so subsequent reads return them
    _state = {"obj": None}
    _orig_add = fake_db.add

    def _tracking_add(obj):
        _orig_add(obj)
        if isinstance(obj, OrgSettings):
            _state["obj"] = obj

    fake_db.add = _tracking_add
    fake_db.on_execute(lambda stmt: FakeResult(scalar=_state["obj"]))

    update_resp = client.put(
        "/api/v1/org/settings",
        json={
            "enforce_service_duration_rule": True,
            "min_service_duration_years": 0.5,
            "enforce_min_vested_to_exercise": True,
            "min_vested_shares_to_exercise": 1000,
        },
    )
    assert update_resp.status_code == 200
    update_data = update_resp.json()["data"]
    assert update_data["enforce_service_duration_rule"] is True
    assert update_data["min_service_duration_years"] == "0.5"
    assert update_data["enforce_min_vested_to_exercise"] is True
    assert update_data["min_vested_shares_to_exercise"] == 1000

    get_resp = client.get("/api/v1/org/settings")
    assert get_resp.status_code == 200
    get_data = get_resp.json()["data"]
    assert get_data["min_service_duration_years"] == "0.5"
    assert get_data["min_vested_shares_to_exercise"] == 1000


@pytest.mark.asyncio
async def test_org_settings_update_writes_audit_log():
    db = FakeAsyncSession()
    ctx = deps.TenantContext(org_id="default")
    settings_obj = OrgSettings(
        org_id="default",
        allowed_repayment_methods=["INTEREST_ONLY", "BALLOON", "PRINCIPAL_AND_INTEREST"],
        min_loan_term_months=6,
        max_loan_term_months=60,
        allowed_interest_types=["FIXED", "VARIABLE"],
        fixed_interest_rate_annual_percent=0,
        require_down_payment=False,
    )
    db.on_execute_return(FakeResult(scalar=settings_obj))
    payload = OrgSettingsUpdate(
        allow_profile_edit=False,
    )
    await settings_service.update_org_settings(
        db,
        ctx,
        payload,
        actor_id="actor-1",
    )
    assert any(isinstance(obj, AuditLog) for obj in db.added)
