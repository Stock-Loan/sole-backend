from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from conftest import FakeAsyncSession, FakeResult, entity_handler, make_user

from app.api.v1.routers import loan_admin
from app.main import app
from app.models.loan_application import LoanApplication
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.schemas.loan import (
    LoanApplicationStatus,
    LoanWorkflowStageStatus,
    LoanWorkflowStageType,
)
from app.schemas.stock import EligibilityResult, StockSummaryResponse
from app.services import loan_queue, loan_applications, loan_workflow, stock_summary


@pytest.fixture(autouse=True)
def _allow_all(allow_all_permissions):
    pass


_APPLICATION_DEFAULTS = dict(
    org_id="default",
    status=LoanApplicationStatus.SUBMITTED.value,
    as_of_date=date(2025, 12, 31),
    selection_mode="SHARES",
    selection_value_snapshot=Decimal("10"),
    shares_to_exercise=10,
    total_exercisable_shares_snapshot=100,
    purchase_price=Decimal("12.50"),
    down_payment_amount=Decimal("0"),
    loan_principal=Decimal("12.50"),
    interest_type="FIXED",
    repayment_method="INTEREST_ONLY",
    term_months=12,
    nominal_annual_rate_percent=Decimal("8.5"),
    estimated_monthly_payment=Decimal("0.09"),
    total_payable_amount=Decimal("13.58"),
    total_interest_amount=Decimal("1.08"),
    org_settings_snapshot={},
    eligibility_result_snapshot={},
)


def _application(**overrides) -> LoanApplication:
    fields = {**_APPLICATION_DEFAULTS, "id": uuid4(), "org_membership_id": uuid4()}
    fields.update(overrides)
    return LoanApplication(**fields)


def test_hr_queue_endpoint(monkeypatch, client_with_permissions, fake_db):
    application = _application()

    async def _list_queue(*args, **kwargs):
        return [application], 1

    monkeypatch.setattr(loan_queue, "list_queue", _list_queue)

    resp = client_with_permissions.get("/api/v1/org/loans/queue/hr")
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(application.id)


def test_hr_review_detail_endpoint(monkeypatch, client_with_permissions, fake_db):
    application_id = uuid4()
    application = _application(id=application_id)
    application.workflow_stages = [
        LoanWorkflowStage(
            id=uuid4(),
            org_id="default",
            loan_application_id=application_id,
            stage_type=LoanWorkflowStageType.HR_REVIEW.value,
            status=LoanWorkflowStageStatus.PENDING.value,
        )
    ]

    async def _get_application(*args, **kwargs):
        return application

    async def _summary(*args, **kwargs):
        return StockSummaryResponse(
            org_membership_id=application.org_membership_id,
            total_granted_shares=100,
            total_vested_shares=100,
            total_unvested_shares=0,
            next_vesting_event=None,
            eligibility_result=EligibilityResult(
                eligible_to_exercise=True,
                total_granted_shares=100,
                total_vested_shares=100,
                total_unvested_shares=0,
                reasons=[],
            ),
            grants=[],
        )

    monkeypatch.setattr(loan_applications, "get_application_with_related", _get_application)
    monkeypatch.setattr(stock_summary, "build_stock_summary", _summary)

    resp = client_with_permissions.get(f"/api/v1/org/loans/{application_id}/hr")
    assert resp.status_code == 200
    assert resp.json()["data"]["hr_stage"]["stage_type"] == "HR_REVIEW"


def test_hr_stage_completion_requires_document(client_with_permissions, fake_db):
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=uuid4(),
        stage_type=LoanWorkflowStageType.HR_REVIEW.value,
        status=LoanWorkflowStageStatus.PENDING.value,
    )
    fake_db.on_execute(entity_handler(LoanWorkflowStage, FakeResult(scalar=stage)))
    fake_db.on_execute(entity_handler(LoanDocument, FakeResult(scalar=None)))

    resp = client_with_permissions.patch(
        f"/api/v1/org/loans/{stage.loan_application_id}/hr",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 400


def test_activation_runs_after_stage_completion(monkeypatch, client_with_permissions, fake_db):
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=uuid4(),
        stage_type=LoanWorkflowStageType.HR_REVIEW.value,
        status=LoanWorkflowStageStatus.PENDING.value,
    )
    document = LoanDocument(
        id=uuid4(),
        org_id="default",
        loan_application_id=stage.loan_application_id,
        stage_type=LoanWorkflowStageType.HR_REVIEW.value,
        document_type="NOTICE_OF_STOCK_OPTION_GRANT",
        file_name="doc.pdf",
        storage_path_or_url="s3://bucket/doc.pdf",
    )
    fake_db.on_execute(entity_handler(LoanWorkflowStage, FakeResult(scalar=stage)))
    fake_db.on_execute(entity_handler(LoanDocument, FakeResult(scalar=document)))

    async def _get_application(*args, **kwargs):
        return _application(id=stage.loan_application_id)

    async def _activate(*args, **kwargs):
        return True

    monkeypatch.setattr(loan_admin, "_get_application_or_404", _get_application)
    monkeypatch.setattr(loan_workflow, "try_activate_loan", _activate)

    resp = client_with_permissions.patch(
        f"/api/v1/org/loans/{stage.loan_application_id}/hr",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 200


def test_hr_document_upload_rejects_wrong_type(monkeypatch, client_with_permissions, fake_db):
    async def _get_application(*args, **kwargs):
        return _application()

    monkeypatch.setattr(loan_admin, "_get_application_or_404", _get_application)

    resp = client_with_permissions.post(
        f"/api/v1/org/loans/{uuid4()}/documents/hr",
        json={
            "document_type": "PAYMENT_INSTRUCTIONS",
            "file_name": "doc.pdf",
            "storage_path_or_url": "s3://bucket/doc.pdf",
        },
    )
    assert resp.status_code == 400


def test_finance_stage_completion_requires_document(client_with_permissions, fake_db):
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=uuid4(),
        stage_type=LoanWorkflowStageType.FINANCE_PROCESSING.value,
        status=LoanWorkflowStageStatus.PENDING.value,
    )
    fake_db.on_execute(entity_handler(LoanWorkflowStage, FakeResult(scalar=stage)))
    fake_db.on_execute(entity_handler(LoanDocument, FakeResult(scalar=None)))

    resp = client_with_permissions.patch(
        f"/api/v1/org/loans/{stage.loan_application_id}/finance",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 400


def test_finance_document_upload_rejects_wrong_type(monkeypatch, client_with_permissions, fake_db):
    async def _get_application(*args, **kwargs):
        return _application()

    monkeypatch.setattr(loan_admin, "_get_application_or_404", _get_application)

    resp = client_with_permissions.post(
        f"/api/v1/org/loans/{uuid4()}/documents/finance",
        json={
            "document_type": "NOTICE_OF_STOCK_OPTION_GRANT",
            "file_name": "doc.pdf",
            "storage_path_or_url": "s3://bucket/doc.pdf",
        },
    )
    assert resp.status_code == 400


def test_legal_queue_endpoint(monkeypatch, client_with_permissions, fake_db):
    application = _application()

    async def _list_queue(*args, **kwargs):
        return [application], 1

    monkeypatch.setattr(loan_queue, "list_queue", _list_queue)

    resp = client_with_permissions.get("/api/v1/org/loans/queue/legal")
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(application.id)


def test_legal_stage_completion_requires_documents(client_with_permissions, fake_db):
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=uuid4(),
        stage_type=LoanWorkflowStageType.LEGAL_EXECUTION.value,
        status=LoanWorkflowStageStatus.PENDING.value,
    )
    fake_db.on_execute(entity_handler(LoanWorkflowStage, FakeResult(scalar=stage)))

    resp = client_with_permissions.patch(
        f"/api/v1/org/loans/{stage.loan_application_id}/legal",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 400


def test_legal_document_upload_rejects_wrong_type(monkeypatch, client_with_permissions, fake_db):
    async def _get_application(*args, **kwargs):
        return _application()

    monkeypatch.setattr(loan_admin, "_get_application_or_404", _get_application)

    resp = client_with_permissions.post(
        f"/api/v1/org/loans/{uuid4()}/documents/legal",
        json={
            "document_type": "PAYMENT_INSTRUCTIONS",
            "file_name": "doc.pdf",
            "storage_path_or_url": "s3://bucket/doc.pdf",
        },
    )
    assert resp.status_code == 400
