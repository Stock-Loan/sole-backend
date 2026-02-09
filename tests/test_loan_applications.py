from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from conftest import (
    FakeAsyncSession,
    FakeResult,
    entity_handler,
    make_membership,
    make_profile,
    make_user,
)

from app.api import deps
from app.models.loan_application import LoanApplication
from app.models.loan_document import LoanDocument
from app.models.loan_workflow_stage import LoanWorkflowStage
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
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
from app.services import loan_applications, loan_quotes, settings as settings_service


@pytest.fixture(autouse=True)
def _allow_all(allow_all_permissions):
    pass


def _org_settings() -> OrgSettings:
    return OrgSettings(
        org_id="default",
        allow_user_data_export=True,
        allow_profile_edit=True,
        require_two_factor=False,
        audit_log_retention_days=180,
        inactive_user_retention_days=180,
        enforce_service_duration_rule=False,
        min_service_duration_years=None,
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


def _make_quote() -> LoanQuoteResponse:
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


_APPLICATION_DEFAULTS = dict(
    org_id="default",
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


def _application(membership_id, **overrides) -> LoanApplication:
    fields = {**_APPLICATION_DEFAULTS, "id": uuid4(), "org_membership_id": membership_id}
    fields.update(overrides)
    return LoanApplication(**fields)


def _user_with_profile(marital_status: str | None = None):
    """Create a user with properly wired membership and profile."""
    user = make_user()
    membership = make_membership(user=user)
    make_profile(membership=membership, marital_status=marital_status)
    user.memberships = [membership]
    return user, membership


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_create_loan_application_draft(fake_db, test_user, client, monkeypatch):
    membership = OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=test_user.id,
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
    )
    fake_db.on_execute(entity_handler(OrgMembership, FakeResult(scalar=membership)))

    async def _quote(*args, **kwargs):
        return _make_quote()

    async def _settings(*args, **kwargs):
        return _org_settings()

    monkeypatch.setattr(loan_quotes, "calculate_loan_quote", _quote)
    monkeypatch.setattr(settings_service, "get_org_settings", _settings)

    resp = client.post(
        "/api/v1/me/loan-applications",
        json={
            "selection_mode": "SHARES",
            "selection_value": "10",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["shares_to_exercise"] == 10


def test_list_loan_applications(fake_db, test_user, client):
    membership = OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=test_user.id,
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
    )
    application = _application(membership.id)
    fake_db.on_execute(entity_handler(OrgMembership, FakeResult(scalar=membership)))
    fake_db.on_execute(entity_handler(LoanApplication, FakeResult(items=[application])))

    resp = client.get("/api/v1/me/loan-applications")
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] == 1


def test_loan_applications_forbidden(deny_all_permissions, fake_db, test_user, override_deps):
    from fastapi.testclient import TestClient
    from app.main import app

    membership = OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=test_user.id,
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
    )
    fake_db.on_execute(entity_handler(OrgMembership, FakeResult(scalar=membership)))

    client = TestClient(app)
    resp = client.get("/api/v1/me/loan-applications")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_rejects_non_draft():
    user = make_user()
    membership = make_membership(user=user)
    application = _application(
        membership.id,
        status=LoanApplicationStatus.SUBMITTED.value,
    )

    with pytest.raises(ValueError):
        await loan_applications.update_draft_application(
            FakeAsyncSession(),
            deps.TenantContext(org_id="default"),
            membership,
            application,
            LoanApplicationDraftUpdate(),
        )


@pytest.mark.asyncio
async def test_submit_requires_spouse_info(monkeypatch):
    user, membership = _user_with_profile(marital_status="MARRIED")
    application = _application(
        membership.id,
        marital_status_snapshot="MARRIED",
    )

    async def _settings(*args, **kwargs):
        return _org_settings()

    async def _quote(*args, **kwargs):
        return _make_quote()

    monkeypatch.setattr(settings_service, "get_org_settings", _settings)
    monkeypatch.setattr(loan_quotes, "calculate_loan_quote", _quote)

    with pytest.raises(loan_quotes.LoanQuoteError) as exc_info:
        await loan_applications.submit_application(
            FakeAsyncSession(),
            deps.TenantContext(org_id="default"),
            membership,
            application,
            user,
        )
    assert exc_info.value.code == "spouse_info_required"


@pytest.mark.asyncio
async def test_submit_rejects_marital_status_mismatch():
    user, membership = _user_with_profile(marital_status="MARRIED")
    application = _application(
        membership.id,
        marital_status_snapshot="SINGLE",
    )

    with pytest.raises(loan_quotes.LoanQuoteError) as exc_info:
        await loan_applications.submit_application(
            FakeAsyncSession(),
            deps.TenantContext(org_id="default"),
            membership,
            application,
            user,
        )
    assert exc_info.value.code == "marital_status_mismatch"


@pytest.mark.asyncio
async def test_submit_single_employee_no_spouse_required(monkeypatch):
    user, membership = _user_with_profile(marital_status="SINGLE")
    application = _application(
        membership.id,
        marital_status_snapshot="SINGLE",
    )

    async def _settings(*args, **kwargs):
        return _org_settings()

    async def _quote(*args, **kwargs):
        return _make_quote()

    monkeypatch.setattr(settings_service, "get_org_settings", _settings)
    monkeypatch.setattr(loan_quotes, "calculate_loan_quote", _quote)

    submitted = await loan_applications.submit_application(
        FakeAsyncSession(),
        deps.TenantContext(org_id="default"),
        membership,
        application,
        user,
    )
    assert submitted.status == LoanApplicationStatus.SUBMITTED.value


def test_loan_application_serializes_workflow_and_documents():
    application_id = uuid4()
    membership_id = uuid4()
    application = _application(
        membership_id,
        id=application_id,
        selection_value_snapshot=Decimal("10"),
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
    user, membership = _user_with_profile(marital_status="SINGLE")
    application = _application(
        membership.id,
        selection_value_snapshot=Decimal("10"),
        marital_status_snapshot="SINGLE",
    )

    async def _settings(*args, **kwargs):
        return _org_settings()

    async def _quote(*args, **kwargs):
        return _make_quote()

    monkeypatch.setattr(settings_service, "get_org_settings", _settings)
    monkeypatch.setattr(loan_quotes, "calculate_loan_quote", _quote)

    db = FakeAsyncSession()
    db.on_execute(entity_handler(LoanWorkflowStage, FakeResult(items=[])))

    submitted = await loan_applications.submit_application(
        db, deps.TenantContext(org_id="default"), membership, application, user
    )
    assert submitted.status == LoanApplicationStatus.SUBMITTED.value
    stages_added = [obj for obj in db.added if isinstance(obj, LoanWorkflowStage)]
    assert {stage.stage_type for stage in stages_added} == {
        LoanWorkflowStageType.HR_REVIEW.value,
        LoanWorkflowStageType.FINANCE_PROCESSING.value,
        LoanWorkflowStageType.LEGAL_EXECUTION.value,
    }


@pytest.mark.asyncio
async def test_submit_idempotent_does_not_duplicate_stages():
    user = make_user()
    membership = make_membership(user=user)
    application = _application(
        membership.id,
        status=LoanApplicationStatus.SUBMITTED.value,
        selection_value_snapshot=Decimal("10"),
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

    db = FakeAsyncSession()
    db.on_execute(entity_handler(LoanWorkflowStage, FakeResult(items=existing_stages)))

    result = await loan_applications.submit_application(
        db,
        deps.TenantContext(org_id="default"),
        membership,
        application,
        user,
        idempotency_key="same-key",
    )
    assert result.status == LoanApplicationStatus.SUBMITTED.value
    stages_added = [obj for obj in db.added if isinstance(obj, LoanWorkflowStage)]
    assert stages_added == []
