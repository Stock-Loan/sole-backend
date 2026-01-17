from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict, EmailStr, field_validator
from uuid import UUID

from app.schemas.common import MaritalStatus, normalize_marital_status
from app.schemas.settings import LoanInterestType, LoanRepaymentMethod
from app.schemas.stock import EligibilityResult, StockSummaryResponse


class LoanSelectionMode(str, Enum):
    PERCENT = "PERCENT"
    SHARES = "SHARES"


class LoanApplicationStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"
    IN_REVIEW = "IN_REVIEW"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"


class LoanWorkflowStageType(str, Enum):
    HR_REVIEW = "HR_REVIEW"
    FINANCE_PROCESSING = "FINANCE_PROCESSING"
    LEGAL_EXECUTION = "LEGAL_EXECUTION"
    LEGAL_POST_ISSUANCE = "LEGAL_POST_ISSUANCE"
    BORROWER_83B_ELECTION = "BORROWER_83B_ELECTION"


class LoanWorkflowStageStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class LoanDocumentType(str, Enum):
    NOTICE_OF_STOCK_OPTION_GRANT = "NOTICE_OF_STOCK_OPTION_GRANT"
    PAYMENT_INSTRUCTIONS = "PAYMENT_INSTRUCTIONS"
    PAYMENT_CONFIRMATION = "PAYMENT_CONFIRMATION"
    STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT = "STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT"
    SECURED_PROMISSORY_NOTE = "SECURED_PROMISSORY_NOTE"
    SPOUSE_PARTNER_CONSENT = "SPOUSE_PARTNER_CONSENT"
    STOCK_POWER_AND_ASSIGNMENT = "STOCK_POWER_AND_ASSIGNMENT"
    INVESTMENT_REPRESENTATION_STATEMENT = "INVESTMENT_REPRESENTATION_STATEMENT"
    SHARE_CERTIFICATE = "SHARE_CERTIFICATE"
    SECTION_83B_ELECTION = "SECTION_83B_ELECTION"


class LoanQuoteRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    selection_mode: LoanSelectionMode
    selection_value: Decimal = Field(gt=0)
    as_of_date: date | None = None
    desired_interest_type: LoanInterestType | None = None
    desired_repayment_method: LoanRepaymentMethod | None = None
    desired_term_months: int | None = Field(default=None, ge=1)


class LoanWhatIfRequest(LoanQuoteRequest):
    org_membership_id: UUID | None = None


class LoanQuoteOption(BaseModel):
    model_config = ConfigDict(use_enum_values=True, json_encoders={Decimal: lambda value: str(value)})

    interest_type: LoanInterestType
    repayment_method: LoanRepaymentMethod
    term_months: int
    nominal_annual_rate: Decimal
    estimated_monthly_payment: Decimal
    total_payable: Decimal
    total_interest: Decimal


class LoanShareAllocation(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})

    grant_id: UUID
    grant_date: date
    shares: int
    exercise_price: Decimal
    purchase_price: Decimal


class LoanQuoteResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True, json_encoders={Decimal: lambda value: str(value)})

    as_of_date: date
    selection_mode: LoanSelectionMode
    selection_value: Decimal
    total_exercisable_shares: int
    shares_to_exercise: int
    purchase_price: Decimal
    down_payment_amount: Decimal
    loan_principal: Decimal
    options: list[LoanQuoteOption]
    eligibility_result: EligibilityResult
    allocation_strategy: str = "OLDEST_VESTED_FIRST"
    allocation: list[LoanShareAllocation] = Field(default_factory=list)


class LoanApplicationDraftCreate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    selection_mode: LoanSelectionMode
    selection_value: Decimal = Field(gt=0)
    as_of_date: date | None = None
    desired_interest_type: LoanInterestType | None = None
    desired_repayment_method: LoanRepaymentMethod | None = None
    desired_term_months: int | None = Field(default=None, ge=1)
    marital_status_snapshot: MaritalStatus | None = None
    spouse_first_name: str | None = None
    spouse_middle_name: str | None = None
    spouse_last_name: str | None = None
    spouse_email: EmailStr | None = None
    spouse_phone: str | None = None
    spouse_address: str | None = None

    @field_validator("marital_status_snapshot", mode="before")
    @classmethod
    def _normalize_marital_status(cls, value):
        return normalize_marital_status(value)

    @field_validator("spouse_email")
    @classmethod
    def _normalize_spouse_email(cls, value):
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("spouse_phone")
    @classmethod
    def _validate_spouse_phone(cls, value):
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        if len(digits) < 7:
            raise ValueError("spouse_phone must include at least 7 digits")
        return cleaned


class LoanApplicationDraftUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    selection_mode: LoanSelectionMode | None = None
    selection_value: Decimal | None = Field(default=None, gt=0)
    as_of_date: date | None = None
    desired_interest_type: LoanInterestType | None = None
    desired_repayment_method: LoanRepaymentMethod | None = None
    desired_term_months: int | None = Field(default=None, ge=1)
    marital_status_snapshot: MaritalStatus | None = None
    spouse_first_name: str | None = None
    spouse_middle_name: str | None = None
    spouse_last_name: str | None = None
    spouse_email: EmailStr | None = None
    spouse_phone: str | None = None
    spouse_address: str | None = None

    @field_validator("marital_status_snapshot", mode="before")
    @classmethod
    def _normalize_marital_status(cls, value):
        return normalize_marital_status(value)

    @field_validator("spouse_email")
    @classmethod
    def _normalize_spouse_email(cls, value):
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("spouse_phone")
    @classmethod
    def _validate_spouse_phone(cls, value):
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        if len(digits) < 7:
            raise ValueError("spouse_phone must include at least 7 digits")
        return cleaned


class LoanApplicantSummaryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    org_membership_id: UUID
    user_id: UUID
    full_name: str
    email: EmailStr
    employee_id: str | None = None
    department_id: UUID | None = None
    department_name: str | None = None


class LoanStageAssigneeSummaryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    full_name: str
    email: EmailStr


class LoanWorkflowStageSelfDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    stage_type: LoanWorkflowStageType
    status: LoanWorkflowStageStatus
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class LoanDocumentSelfDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    document_type: LoanDocumentType
    file_name: str
    storage_path_or_url: str
    uploaded_at: datetime | None = None


class LoanApplicationSummaryDTO(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    id: UUID
    org_membership_id: UUID
    applicant: LoanApplicantSummaryDTO
    status: LoanApplicationStatus
    version: int
    as_of_date: date
    shares_to_exercise: int
    total_exercisable_shares_snapshot: int
    purchase_price: Decimal
    down_payment_amount: Decimal
    loan_principal: Decimal
    estimated_monthly_payment: Decimal
    total_payable_amount: Decimal
    interest_type: LoanInterestType
    repayment_method: LoanRepaymentMethod
    term_months: int
    current_stage_type: LoanWorkflowStageType | None = None
    current_stage_status: LoanWorkflowStageStatus | None = None
    current_stage_assignee: LoanStageAssigneeSummaryDTO | None = None
    current_stage_assigned_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LoanApplicationSelfSummaryDTO(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    id: UUID
    status: LoanApplicationStatus
    as_of_date: date
    shares_to_exercise: int
    loan_principal: Decimal
    estimated_monthly_payment: Decimal
    total_payable_amount: Decimal
    interest_type: LoanInterestType
    repayment_method: LoanRepaymentMethod
    term_months: int
    current_stage_type: LoanWorkflowStageType | None = None
    current_stage_status: LoanWorkflowStageStatus | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LoanApplicationDTO(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    id: UUID
    org_id: str
    org_membership_id: UUID
    status: LoanApplicationStatus
    decision_reason: str | None = None
    activation_date: datetime | None = None
    election_83b_due_date: date | None = None
    version: int
    as_of_date: date
    selection_mode: LoanSelectionMode
    selection_value_snapshot: Decimal
    shares_to_exercise: int
    total_exercisable_shares_snapshot: int
    purchase_price: Decimal
    down_payment_amount: Decimal
    loan_principal: Decimal
    policy_version_snapshot: int | None = None
    interest_type: LoanInterestType
    repayment_method: LoanRepaymentMethod
    term_months: int
    nominal_annual_rate_percent: Decimal
    estimated_monthly_payment: Decimal
    total_payable_amount: Decimal
    total_interest_amount: Decimal
    quote_inputs_snapshot: dict
    quote_option_snapshot: dict
    allocation_strategy: str
    allocation_snapshot: list[dict]
    org_settings_snapshot: dict
    eligibility_result_snapshot: dict
    marital_status_snapshot: MaritalStatus | None = None
    spouse_first_name: str | None = None
    spouse_middle_name: str | None = None
    spouse_last_name: str | None = None
    spouse_email: EmailStr | None = None
    spouse_phone: str | None = None
    spouse_address: str | None = None
    workflow_stages: list["LoanWorkflowStageDTO"] | None = None
    documents: list["LoanDocumentDTO"] | None = None
    has_share_certificate: bool | None = None
    has_83b_election: bool | None = None
    days_until_83b_due: int | None = None
    applicant: LoanApplicantSummaryDTO | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LoanApplicationSelfDTO(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    id: UUID
    status: LoanApplicationStatus
    as_of_date: date
    selection_mode: LoanSelectionMode
    selection_value_snapshot: Decimal
    shares_to_exercise: int
    total_exercisable_shares_snapshot: int
    purchase_price: Decimal
    down_payment_amount: Decimal
    loan_principal: Decimal
    estimated_monthly_payment: Decimal
    total_payable_amount: Decimal
    total_interest_amount: Decimal
    interest_type: LoanInterestType
    repayment_method: LoanRepaymentMethod
    term_months: int
    allocation_strategy: str
    allocation_snapshot: list[dict]
    eligibility_result_snapshot: dict
    current_stage_type: LoanWorkflowStageType | None = None
    current_stage_status: LoanWorkflowStageStatus | None = None
    workflow_stages: list[LoanWorkflowStageSelfDTO] | None = None
    documents: list[LoanDocumentSelfDTO] | None = None
    has_share_certificate: bool | None = None
    has_83b_election: bool | None = None
    days_until_83b_due: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LoanApplicationListResponse(BaseModel):
    items: list[LoanApplicationSummaryDTO]
    total: int


class LoanApplicationSelfListResponse(BaseModel):
    items: list[LoanApplicationSelfSummaryDTO]
    total: int


class LoanActivationMaintenanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    checked: int
    activated: int
    skipped: int
    activated_ids: list[UUID] = Field(default_factory=list)
    post_issuance_completed: int = 0
    post_issuance_completed_ids: list[UUID] = Field(default_factory=list)


class LoanDashboardSummary(BaseModel):
    org_id: str
    as_of: date
    total_loans: int
    status_counts: dict[str, int]
    open_stage_counts: dict[str, int]
    created_last_30_days: int
    activated_last_30_days: int


class LoanScheduleEntry(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})

    period: int
    due_date: date | None
    payment: Decimal
    principal: Decimal
    interest: Decimal
    remaining_balance: Decimal


class LoanScheduleResponse(BaseModel):
    model_config = ConfigDict(
        use_enum_values=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    loan_id: UUID
    as_of_date: date
    repayment_method: LoanRepaymentMethod
    term_months: int
    principal: Decimal
    annual_rate_percent: Decimal
    estimated_monthly_payment: Decimal
    entries: list[LoanScheduleEntry]


class LoanWorkflowStageDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    org_id: str
    loan_application_id: UUID
    stage_type: LoanWorkflowStageType
    status: LoanWorkflowStageStatus
    assigned_role_hint: str | None = None
    completed_by_user_id: UUID | None = None
    completed_at: datetime | None = None
    assigned_to_user_id: UUID | None = None
    assigned_by_user_id: UUID | None = None
    assigned_at: datetime | None = None
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LoanDocumentDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    org_id: str
    loan_application_id: UUID
    stage_type: LoanWorkflowStageType
    document_type: LoanDocumentType
    file_name: str
    storage_path_or_url: str
    uploaded_by_user_id: UUID | None = None
    uploaded_at: datetime | None = None
    created_at: datetime | None = None


LoanApplicationDTO.model_rebuild()


class LoanDocumentGroup(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    stage_type: LoanWorkflowStageType
    documents: list[LoanDocumentDTO]


class LoanDocumentListResponse(BaseModel):
    loan_id: UUID
    total: int
    groups: list[LoanDocumentGroup]


class LoanWorkflowStageUpdateRequest(BaseModel):
    status: LoanWorkflowStageStatus
    notes: str | None = None


class LoanWorkflowStageAssignRequest(BaseModel):
    assignee_user_id: UUID | None = None


class LoanDocumentCreateRequest(BaseModel):
    document_type: LoanDocumentType
    file_name: str = Field(min_length=1)
    storage_path_or_url: str = Field(min_length=1)


class LoanAdminUpdateRequest(BaseModel):
    status: LoanApplicationStatus | None = None
    decision_reason: str | None = None


class LoanHRReviewResponse(BaseModel):
    loan_application: LoanApplicationDTO
    stock_summary: StockSummaryResponse
    hr_stage: LoanWorkflowStageDTO | None = None


class LoanFinanceReviewResponse(BaseModel):
    loan_application: LoanApplicationDTO
    finance_stage: LoanWorkflowStageDTO | None = None


class LoanLegalReviewResponse(BaseModel):
    loan_application: LoanApplicationDTO
    legal_stage: LoanWorkflowStageDTO | None = None
