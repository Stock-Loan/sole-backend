from uuid import uuid4

import pytest

from conftest import FakeAsyncSession, FakeResult, make_user

from app.models.org_membership import OrgMembership
from app.schemas.stock import EligibilityResult, StockSummaryResponse
from app.services import stock_summary


@pytest.fixture(autouse=True)
def _allow_all(allow_all_permissions):
    pass


def test_me_stock_summary_404_when_no_membership(fake_db, client):
    fake_db.on_execute_return(FakeResult(scalar=None))

    resp = client.get("/api/v1/me/stock/summary")
    assert resp.status_code == 404


def test_me_stock_summary_returns_summary(fake_db, test_user, client, monkeypatch):
    membership = OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=test_user.id,
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
    )
    fake_db.on_execute_return(FakeResult(scalar=membership))

    summary = StockSummaryResponse(
        org_membership_id=membership.id,
        total_granted_shares=0,
        total_vested_shares=0,
        total_unvested_shares=0,
        next_vesting_event=None,
        eligibility_result=EligibilityResult(
            eligible_to_exercise=False,
            total_granted_shares=0,
            total_vested_shares=0,
            total_unvested_shares=0,
            reasons=[],
        ),
        grants=[],
    )

    async def _stub_build(*args, **kwargs):
        return summary

    monkeypatch.setattr(stock_summary, "build_stock_summary", _stub_build)

    resp = client.get("/api/v1/me/stock/summary")
    assert resp.status_code == 200
    assert resp.json()["data"]["org_membership_id"] == str(membership.id)


def test_me_stock_summary_forbidden(deny_all_permissions, fake_db, test_user, override_deps):
    from fastapi.testclient import TestClient
    from app.main import app

    fake_db.on_execute_return(FakeResult(scalar=None))
    client = TestClient(app)
    resp = client.get("/api/v1/me/stock/summary")
    assert resp.status_code == 403
