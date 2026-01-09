import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from app.api import deps
from app.db.session import get_db
from app.main import app
from app.models.loan_application import LoanApplication
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.schemas.loan import (
    LoanApplicationDTO,
    LoanApplicationDraftUpdate,
    LoanApplicationStatus,
    LoanDocumentType,
    LoanQuoteOption,
    LoanQuoteResponse,
    LoanSelectionMode,
    LoanWorkflowStageStatus,
    LoanWorkflowStageType,
)
from app.schemas.settings import LoanInterestType, LoanRepaymentMethod
from app.schemas.stock import EligibilityResult
from app.services import authz, loan_applications, loan_quotes, settings as settings_service
from app.models.org_settings import OrgSettings


class FakeResult:
    def __init__(self, scalar=None, items=None):
        self._scalar = scalar
        self._items = items or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._items


class FakeSession:
    def __init__(self, membership=None, applications=None, application=None, stages=None):
        self.membership = membership
        self.applications = applications or []
        self.application = application
        self.stages = stages or []
        self.added = []

    async def execute(self, stmt):
        entity = getattr(stmt, "column_descriptions", [{}])[0].get("entity")
        if entity is OrgMembership:
            return FakeResult(scalar=self.membership)
        if entity is LoanApplication:
            if self.application is not None:
                return FakeResult(scalar=self.application)
            return FakeResult(items=self.applications)
        if entity is LoanWorkflowStage:
            return FakeResult(items=self.stages)
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
            email="user@example.com",
            full_name="Test User",
            hashed_password="hash",
            is_active=True,
        )
        self.id = uuid4()


def override_dependencies(session: FakeSession, user: DummyUser) -> None:
    async def _get_db():
        yield session

    async def _get_ctx():
        return deps.TenantContext(org_id="default")

    async def _require_user():
        return user

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx
    app.dependency_overrides[deps.require_authenticated_user] = _require_user
    app.dependency_overrides[deps.get_current_user] = _require_user


def clear_overrides() -> None:
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(deps.get_tenant_context, None)
    app.dependency_overrides.pop(deps.require_authenticated_user, None)
    app.dependency_overrides.pop(deps.get_current_user, None)


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    async def _allow(*args, **kwargs):
        return True

    monkeypatch.setattr(authz, "check_permission", _allow)
    yield
    clear_overrides()


def _membership(user: DummyUser) -> OrgMembership:
    return OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=user.id,
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
    )


def _org_settings() -> OrgSettings:
    return OrgSettings(
        org_id="default",
        allow_user_data_export=True,
        allow_profile_edit=True,
        require_two_factor=False,
        audit_log_retention_days=180,
        inactive_user_retention_days=180,
        enforce_service_duration_rule=False,
        min_service_duration_days=None,
        enforce_min_vested_to_exercise=False,
        min_vested_shares_to_exercise=None,
        allowed_repayment_methods=["INTEREST_ONLY"],
        min_loan_term_months=12,
        max_loan_term_months=60,
        allowed_interest_types=["FIXED"],
        fixed_interest_rate_annual_percent=Decimal("8.5"),
        variable_base_rate_annual_percent=None,
        variable_margin_annual_percent=None,
        require_down_payment=False,
        down_payment_percent=None,
    )


def test_create_loan_application_draft(monkeypatch):
    user = DummyUser()
    membership = _membership(user)
    session = FakeSession(membership=membership)
    override_dependencies(session, user)

    quote = LoanQuoteResponse(
        as_of_date=date(2025, 12, 31),
        selection_mode=LoanSelectionMode.SHARES,
        selection_value=Decimal("10"),
        total_exercisable_shares=100,
        shares_to_exercise=10,
        purchase_price=Decimal("12.50"),
        down_payment_amount=Decimal("0"),
        loan_principal=Decimal("12.50"),
        options=[
            LoanQuoteOption(
                interest_type=LoanInterestType.FIXED,
                repayment_method=LoanRepaymentMethod.INTEREST_ONLY,
                term_months=12,
                nominal_annual_rate=Decimal("8.5"),
                estimated_monthly_payment=Decimal("0.09"),
                total_payable=Decimal("13.58"),
                total_interest=Decimal("1.08"),
            )
        ],
        eligibility_result=EligibilityResult(
            eligible_to_exercise=True,
            total_granted_shares=100,
            total_vested_shares=100,
            total_unvested_shares=0,
            reasons=[],
        ),
    )

    async def _quote(*args, **kwargs):
        return quote

    async def _settings(*args, **kwargs):
        return _org_settings()

    monkeypatch.setattr(loan_quotes, "calculate_loan_quote", _quote)
    monkeypatch.setattr(settings_service, "get_org_settings", _settings)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/me/loan-applications",
        json={
            "selection_mode": "SHARES",
            "selection_value": "10",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["shares_to_exercise"] == 10


def test_list_loan_applications():
    user = DummyUser()
    membership = _membership(user)
    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership.id,
        status=LoanApplicationStatus.DRAFT.value,
        as_of_date=date(2025, 12, 31),
        selection_mode="SHARES",
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
    session = FakeSession(membership=membership, applications=[application])
    override_dependencies(session, user)

    client = TestClient(app)
    resp = client.get("/api/v1/me/loan-applications")
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 1


def test_loan_applications_forbidden(monkeypatch):
    async def _deny(*args, **kwargs):
        return False

    monkeypatch.setattr(authz, "check_permission", _deny)
    user = DummyUser()
    session = FakeSession(membership=_membership(user))
    override_dependencies(session, user)

    client = TestClient(app)
    resp = client.get("/api/v1/me/loan-applications")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_rejects_non_draft():
    user = DummyUser()
    membership = _membership(user)
    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership.id,
        status=LoanApplicationStatus.SUBMITTED.value,
        as_of_date=date(2025, 12, 31),
        selection_mode="SHARES",
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
    with pytest.raises(ValueError):
        await loan_applications.update_draft_application(
            FakeSession(),
            deps.TenantContext(org_id="default"),
            membership,
            application,
            LoanApplicationDraftUpdate(),
        )


@pytest.mark.asyncio
async def test_submit_requires_spouse_info(monkeypatch):
    user = DummyUser()
    user.marital_status = "MARRIED"
    membership = _membership(user)
    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership.id,
        status=LoanApplicationStatus.DRAFT.value,
        as_of_date=date(2025, 12, 31),
        selection_mode="SHARES",
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
        marital_status_snapshot="MARRIED",
    )

    async def _settings(*args, **kwargs):
        return _org_settings()

    async def _quote(*args, **kwargs):
        return LoanQuoteResponse(
            as_of_date=date(2025, 12, 31),
            selection_mode=LoanSelectionMode.SHARES,
            selection_value=Decimal("10"),
            total_exercisable_shares=100,
            shares_to_exercise=10,
            purchase_price=Decimal("12.50"),
            down_payment_amount=Decimal("0"),
            loan_principal=Decimal("12.50"),
            options=[
                LoanQuoteOption(
                    interest_type=LoanInterestType.FIXED,
                    repayment_method=LoanRepaymentMethod.INTEREST_ONLY,
                    term_months=12,
                    nominal_annual_rate=Decimal("8.5"),
                    estimated_monthly_payment=Decimal("0.09"),
                    total_payable=Decimal("13.58"),
                    total_interest=Decimal("1.08"),
                )
            ],
            eligibility_result=EligibilityResult(
                eligible_to_exercise=True,
                total_granted_shares=100,
                total_vested_shares=100,
                total_unvested_shares=0,
                reasons=[],
            ),
        )

    monkeypatch.setattr(settings_service, "get_org_settings", _settings)
    monkeypatch.setattr(loan_quotes, "calculate_loan_quote", _quote)

    with pytest.raises(loan_quotes.LoanQuoteError) as exc_info:
        await loan_applications.submit_application(
            FakeSession(), deps.TenantContext(org_id="default"), membership, application, user
        )
    assert exc_info.value.code == "spouse_info_required"


@pytest.mark.asyncio
async def test_submit_rejects_marital_status_mismatch():
    user = DummyUser()
    user.marital_status = "MARRIED"
    membership = _membership(user)
    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership.id,
        status=LoanApplicationStatus.DRAFT.value,
        as_of_date=date(2025, 12, 31),
        selection_mode="SHARES",
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
        marital_status_snapshot="SINGLE",
    )

    with pytest.raises(loan_quotes.LoanQuoteError) as exc_info:
        await loan_applications.submit_application(
            FakeSession(), deps.TenantContext(org_id="default"), membership, application, user
        )
    assert exc_info.value.code == "marital_status_mismatch"


@pytest.mark.asyncio
async def test_submit_single_employee_no_spouse_required(monkeypatch):
    user = DummyUser()
    user.marital_status = "SINGLE"
    membership = _membership(user)
    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership.id,
        status=LoanApplicationStatus.DRAFT.value,
        as_of_date=date(2025, 12, 31),
        selection_mode="SHARES",
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
        marital_status_snapshot="SINGLE",
    )

    async def _settings(*args, **kwargs):
        return _org_settings()

    async def _quote(*args, **kwargs):
        return LoanQuoteResponse(
            as_of_date=date(2025, 12, 31),
            selection_mode=LoanSelectionMode.SHARES,
            selection_value=Decimal("10"),
            total_exercisable_shares=100,
            shares_to_exercise=10,
            purchase_price=Decimal("12.50"),
            down_payment_amount=Decimal("0"),
            loan_principal=Decimal("12.50"),
            options=[
                LoanQuoteOption(
                    interest_type=LoanInterestType.FIXED,
                    repayment_method=LoanRepaymentMethod.INTEREST_ONLY,
                    term_months=12,
                    nominal_annual_rate=Decimal("8.5"),
                    estimated_monthly_payment=Decimal("0.09"),
                    total_payable=Decimal("13.58"),
                    total_interest=Decimal("1.08"),
                )
            ],
            eligibility_result=EligibilityResult(
                eligible_to_exercise=True,
                total_granted_shares=100,
                total_vested_shares=100,
                total_unvested_shares=0,
                reasons=[],
            ),
        )

    monkeypatch.setattr(settings_service, "get_org_settings", _settings)
    monkeypatch.setattr(loan_quotes, "calculate_loan_quote", _quote)

    submitted = await loan_applications.submit_application(
        FakeSession(), deps.TenantContext(org_id="default"), membership, application, user
    )
    assert submitted.status == LoanApplicationStatus.SUBMITTED.value


def test_loan_application_serializes_workflow_and_documents():
    application_id = uuid4()
    membership_id = uuid4()
    application = LoanApplication(
        id=application_id,
        org_id="default",
        org_membership_id=membership_id,
        status=LoanApplicationStatus.DRAFT.value,
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
    stage = LoanWorkflowStage(
        id=uuid4(),
        org_id="default",
        loan_application_id=application_id,
        stage_type=LoanWorkflowStageType.HR_REVIEW.value,
        status=LoanWorkflowStageStatus.PENDING.value,
        assigned_role_hint="HR",
    )
    document = LoanDocument(
        id=uuid4(),
        org_id="default",
        loan_application_id=application_id,
        stage_type=LoanWorkflowStageType.HR_REVIEW.value,
        document_type=LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT.value,
        file_name="notice.pdf",
        storage_path_or_url="s3://bucket/notice.pdf",
    )
    application.workflow_stages = [stage]
    application.documents = [document]

    dto = LoanApplicationDTO.model_validate(application)
    assert dto.workflow_stages is not None
    assert dto.documents is not None
    assert dto.workflow_stages[0].stage_type == LoanWorkflowStageType.HR_REVIEW
    assert dto.documents[0].document_type == LoanDocumentType.NOTICE_OF_STOCK_OPTION_GRANT


@pytest.mark.asyncio
async def test_submit_creates_core_workflow_stages(monkeypatch):
    user = DummyUser()
    user.marital_status = "SINGLE"
    membership = _membership(user)
    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership.id,
        status=LoanApplicationStatus.DRAFT.value,
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
        marital_status_snapshot="SINGLE",
    )

    async def _settings(*args, **kwargs):
        return _org_settings()

    async def _quote(*args, **kwargs):
        return LoanQuoteResponse(
            as_of_date=date(2025, 12, 31),
            selection_mode=LoanSelectionMode.SHARES,
            selection_value=Decimal("10"),
            total_exercisable_shares=100,
            shares_to_exercise=10,
            purchase_price=Decimal("12.50"),
            down_payment_amount=Decimal("0"),
            loan_principal=Decimal("12.50"),
            options=[
                LoanQuoteOption(
                    interest_type=LoanInterestType.FIXED,
                    repayment_method=LoanRepaymentMethod.INTEREST_ONLY,
                    term_months=12,
                    nominal_annual_rate=Decimal("8.5"),
                    estimated_monthly_payment=Decimal("0.09"),
                    total_payable=Decimal("13.58"),
                    total_interest=Decimal("1.08"),
                )
            ],
            eligibility_result=EligibilityResult(
                eligible_to_exercise=True,
                total_granted_shares=100,
                total_vested_shares=100,
                total_unvested_shares=0,
                reasons=[],
            ),
        )

    monkeypatch.setattr(settings_service, "get_org_settings", _settings)
    monkeypatch.setattr(loan_quotes, "calculate_loan_quote", _quote)

    session = FakeSession(stages=[])
    submitted = await loan_applications.submit_application(
        session, deps.TenantContext(org_id="default"), membership, application, user
    )
    assert submitted.status == LoanApplicationStatus.SUBMITTED.value
    stages_added = [obj for obj in session.added if isinstance(obj, LoanWorkflowStage)]
    assert {stage.stage_type for stage in stages_added} == {
        LoanWorkflowStageType.HR_REVIEW.value,
        LoanWorkflowStageType.FINANCE_PROCESSING.value,
        LoanWorkflowStageType.LEGAL_EXECUTION.value,
    }


@pytest.mark.asyncio
async def test_submit_idempotent_does_not_duplicate_stages():
    user = DummyUser()
    membership = _membership(user)
    application = LoanApplication(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership.id,
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
    application.submit_idempotency_key = "same-key"

    existing_stages = [
        LoanWorkflowStage(
            id=uuid4(),
            org_id="default",
            loan_application_id=application.id,
            stage_type=LoanWorkflowStageType.HR_REVIEW.value,
            status=LoanWorkflowStageStatus.PENDING.value,
        ),
        LoanWorkflowStage(
            id=uuid4(),
            org_id="default",
            loan_application_id=application.id,
            stage_type=LoanWorkflowStageType.FINANCE_PROCESSING.value,
            status=LoanWorkflowStageStatus.PENDING.value,
        ),
        LoanWorkflowStage(
            id=uuid4(),
            org_id="default",
            loan_application_id=application.id,
            stage_type=LoanWorkflowStageType.LEGAL_EXECUTION.value,
            status=LoanWorkflowStageStatus.PENDING.value,
        ),
    ]
    session = FakeSession(stages=existing_stages)

    result = await loan_applications.submit_application(
        session,
        deps.TenantContext(org_id="default"),
        membership,
        application,
        user,
        idempotency_key="same-key",
    )
    assert result.status == LoanApplicationStatus.SUBMITTED.value
    stages_added = [obj for obj in session.added if isinstance(obj, LoanWorkflowStage)]
    assert stages_added == []
