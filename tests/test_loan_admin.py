import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.api import deps
from app.api.v1.routers import loan_admin
from app.db.session import get_db
from app.main import app
from app.models.loan_application import LoanApplication
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.models.user import User
from app.schemas.loan import (
    LoanApplicationStatus,
    LoanWorkflowStageStatus,
    LoanWorkflowStageType,
)
from app.schemas.stock import EligibilityResult, StockSummaryResponse
from app.services import loan_queue, loan_applications, loan_workflow, stock_summary


class FakeResult:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class FakeSession:
    def __init__(self, stage=None, document=None):
        self.stage = stage
        self.document = document
        self.added = []

    async def execute(self, stmt):
        entity = getattr(stmt, "column_descriptions", [{}])[0].get("entity")
        if entity is LoanWorkflowStage:
            return FakeResult(scalar=self.stage)
        if entity is LoanDocument:
            return FakeResult(scalar=self.document)
        return FakeResult()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None


class DummyUser(User):
    def __init__(self) -> None:
        super().__init__(
            org_id="default",
            email="hr@example.com",
            full_name="HR User",
            hashed_password="hash",
            is_active=True,
        )
        self.id = uuid4()


def override_dependencies(session: FakeSession, user: DummyUser) -> None:
    async def _get_db():
        yield session

    async def _get_ctx():
        return deps.TenantContext(org_id="default")

    def _require_permission_override(*args, **kwargs):
        async def _dep():
            return user

        return _dep

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx
    app.dependency_overrides[deps.require_permission] = _require_permission_override


def clear_overrides() -> None:
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(deps.get_tenant_context, None)
    app.dependency_overrides.pop(deps.require_permission, None)


def test_hr_queue_endpoint(monkeypatch):
    user = DummyUser()
    session = FakeSession()
    override_dependencies(session, user)

    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=uuid4(),
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

    async def _list_queue(*args, **kwargs):
        return [application], 1

    monkeypatch.setattr(loan_queue, "list_queue", _list_queue)

    client = TestClient(app)
    resp = client.get("/api/v1/org/loans/queue/hr")
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(application.id)

    clear_overrides()


def test_hr_review_detail_endpoint(monkeypatch):
    user = DummyUser()
    session = FakeSession()
    override_dependencies(session, user)

    application_id = uuid4()
    application = LoanApplication(
        id=application_id,
        org_id="default",
        org_membership_id=uuid4(),
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

    client = TestClient(app)
    resp = client.get(f"/api/v1/org/loans/{application_id}/hr")
    assert resp.status_code == 200
    assert resp.json()["data"]["hr_stage"]["stage_type"] == "HR_REVIEW"

    clear_overrides()


def test_hr_stage_completion_requires_document():
    user = DummyUser()
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=uuid4(),
        stage_type=LoanWorkflowStageType.HR_REVIEW.value,
        status=LoanWorkflowStageStatus.PENDING.value,
    )
    session = FakeSession(stage=stage, document=None)
    override_dependencies(session, user)

    client = TestClient(app)
    resp = client.patch(
        f"/api/v1/org/loans/{stage.loan_application_id}/hr",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 400

    clear_overrides()


def test_activation_runs_after_stage_completion(monkeypatch):
    user = DummyUser()
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=uuid4(),
        stage_type=LoanWorkflowStageType.HR_REVIEW.value,
        status=LoanWorkflowStageStatus.PENDING.value,
    )
    session = FakeSession(
        stage=stage,
        document=LoanDocument(
            id=uuid4(),
            org_id="default",
            loan_application_id=stage.loan_application_id,
            stage_type=LoanWorkflowStageType.HR_REVIEW.value,
            document_type="NOTICE_OF_STOCK_OPTION_GRANT",
            file_name="doc.pdf",
            storage_path_or_url="s3://bucket/doc.pdf",
        ),
    )
    override_dependencies(session, user)

    async def _get_application(*args, **kwargs):
        application = LoanApplication(
            id=stage.loan_application_id,
            org_id="default",
            org_membership_id=uuid4(),
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
        return application

    async def _activate(*args, **kwargs):
        return True

    monkeypatch.setattr(loan_admin, "_get_application_or_404", _get_application)
    monkeypatch.setattr(loan_workflow, "try_activate_loan", _activate)

    client = TestClient(app)
    resp = client.patch(
        f"/api/v1/org/loans/{stage.loan_application_id}/hr",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 200

    clear_overrides()


def test_hr_document_upload_rejects_wrong_type(monkeypatch):
    user = DummyUser()
    session = FakeSession()
    override_dependencies(session, user)

    async def _get_application(*args, **kwargs):
        application = LoanApplication(
            id=uuid4(),
            org_id="default",
            org_membership_id=uuid4(),
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
        return application

    monkeypatch.setattr(loan_admin, "_get_application_or_404", _get_application)

    client = TestClient(app)
    resp = client.post(
        f"/api/v1/org/loans/{uuid4()}/documents/hr",
        json={
            "document_type": "PAYMENT_INSTRUCTIONS",
            "file_name": "doc.pdf",
            "storage_path_or_url": "s3://bucket/doc.pdf",
        },
    )
    assert resp.status_code == 400

    clear_overrides()


def test_finance_stage_completion_requires_document():
    user = DummyUser()
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=uuid4(),
        stage_type=LoanWorkflowStageType.FINANCE_PROCESSING.value,
        status=LoanWorkflowStageStatus.PENDING.value,
    )
    session = FakeSession(stage=stage, document=None)
    override_dependencies(session, user)

    client = TestClient(app)
    resp = client.patch(
        f"/api/v1/org/loans/{stage.loan_application_id}/finance",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 400

    clear_overrides()


def test_finance_document_upload_rejects_wrong_type(monkeypatch):
    user = DummyUser()
    session = FakeSession()
    override_dependencies(session, user)

    async def _get_application(*args, **kwargs):
        application = LoanApplication(
            id=uuid4(),
            org_id="default",
            org_membership_id=uuid4(),
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
        return application

    monkeypatch.setattr(loan_admin, "_get_application_or_404", _get_application)

    client = TestClient(app)
    resp = client.post(
        f"/api/v1/org/loans/{uuid4()}/documents/finance",
        json={
            "document_type": "NOTICE_OF_STOCK_OPTION_GRANT",
            "file_name": "doc.pdf",
            "storage_path_or_url": "s3://bucket/doc.pdf",
        },
    )
    assert resp.status_code == 400

    clear_overrides()


def test_legal_queue_endpoint(monkeypatch):
    user = DummyUser()
    session = FakeSession()
    override_dependencies(session, user)

    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=uuid4(),
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

    async def _list_queue(*args, **kwargs):
        return [application], 1

    monkeypatch.setattr(loan_queue, "list_queue", _list_queue)

    client = TestClient(app)
    resp = client.get("/api/v1/org/loans/queue/legal")
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(application.id)

    clear_overrides()


def test_legal_stage_completion_requires_documents(monkeypatch):
    user = DummyUser()
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=uuid4(),
        stage_type=LoanWorkflowStageType.LEGAL_EXECUTION.value,
        status=LoanWorkflowStageStatus.PENDING.value,
    )
    session = FakeSession(stage=stage)
    override_dependencies(session, user)

    client = TestClient(app)
    resp = client.patch(
        f"/api/v1/org/loans/{stage.loan_application_id}/legal",
        json={"status": "COMPLETED"},
    )
    assert resp.status_code == 400

    clear_overrides()


def test_legal_document_upload_rejects_wrong_type(monkeypatch):
    user = DummyUser()
    session = FakeSession()
    override_dependencies(session, user)

    async def _get_application(*args, **kwargs):
        application = LoanApplication(
            id=uuid4(),
            org_id="default",
            org_membership_id=uuid4(),
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
        return application

    monkeypatch.setattr(loan_admin, "_get_application_or_404", _get_application)

    client = TestClient(app)
    resp = client.post(
        f"/api/v1/org/loans/{uuid4()}/documents/legal",
        json={
            "document_type": "PAYMENT_INSTRUCTIONS",
            "file_name": "doc.pdf",
            "storage_path_or_url": "s3://bucket/doc.pdf",
        },
    )
    assert resp.status_code == 400

    clear_overrides()
