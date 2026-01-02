from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict, EmailStr, field_validator
from uuid import UUID

from app.schemas.common import MaritalStatus, normalize_marital_status
from app.schemas.settings import LoanInterestType, LoanRepaymentMethod
from app.schemas.stock import EligibilityResult


class LoanSelectionMode(str, Enum):
    PERCENT = "PERCENT"
    SHARES = "SHARES"


class LoanApplicationStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"


class LoanQuoteRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    selection_mode: LoanSelectionMode
    selection_value: Decimal = Field(gt=0)
    as_of_date: date | None = None
    desired_interest_type: LoanInterestType | None = None
    desired_repayment_method: LoanRepaymentMethod | None = None
    desired_term_months: int | None = Field(default=None, ge=1)


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


class LoanApplicationSummaryDTO(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    id: UUID
    status: LoanApplicationStatus
    version: int
    as_of_date: date
    shares_to_exercise: int
    loan_principal: Decimal
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
    version: int
    as_of_date: date
    selection_mode: LoanSelectionMode
    selection_value_snapshot: Decimal
    shares_to_exercise: int
    total_exercisable_shares_snapshot: int
    purchase_price: Decimal
    down_payment_amount: Decimal
    loan_principal: Decimal
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
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LoanApplicationListResponse(BaseModel):
    items: list[LoanApplicationSummaryDTO]
    total: int
