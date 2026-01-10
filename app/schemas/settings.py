from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict


class LoanRepaymentMethod(str, Enum):
    INTEREST_ONLY = "INTEREST_ONLY"
    BALLOON = "BALLOON"
    PRINCIPAL_AND_INTEREST = "PRINCIPAL_AND_INTEREST"


class LoanInterestType(str, Enum):
    FIXED = "FIXED"
    VARIABLE = "VARIABLE"


class OrgSettingsBase(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    allow_user_data_export: bool = Field(default=True)
    allow_profile_edit: bool = Field(default=True)
    require_two_factor: bool = Field(default=False)
    audit_log_retention_days: int = Field(default=180, ge=0)
    inactive_user_retention_days: int = Field(default=180, ge=0)
    enforce_service_duration_rule: bool = Field(default=False)
    min_service_duration_days: int | None = Field(default=None, ge=0)
    enforce_min_vested_to_exercise: bool = Field(default=False)
    min_vested_shares_to_exercise: int | None = Field(default=None, ge=0)
    allowed_repayment_methods: list[LoanRepaymentMethod] = Field(
        default_factory=lambda: [
            LoanRepaymentMethod.INTEREST_ONLY,
            LoanRepaymentMethod.BALLOON,
            LoanRepaymentMethod.PRINCIPAL_AND_INTEREST,
        ]
    )
    min_loan_term_months: int = Field(default=6, ge=1)
    max_loan_term_months: int = Field(default=60, ge=1)
    allowed_interest_types: list[LoanInterestType] = Field(
        default_factory=lambda: [
            LoanInterestType.FIXED,
            LoanInterestType.VARIABLE,
        ]
    )
    fixed_interest_rate_annual_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    variable_base_rate_annual_percent: Decimal | None = Field(default=None, ge=0, le=100)
    variable_margin_annual_percent: Decimal | None = Field(default=None, ge=0, le=100)
    require_down_payment: bool = Field(default=False)
    down_payment_percent: Decimal | None = Field(default=None, ge=0, le=100)


class OrgSettingsResponse(OrgSettingsBase):
    org_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        json_encoders={Decimal: lambda value: str(value)},
    )


class OrgPolicyResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    org_id: str
    allow_user_data_export: bool
    allow_profile_edit: bool
    require_two_factor: bool
    enforce_service_duration_rule: bool
    min_service_duration_days: int | None = None
    enforce_min_vested_to_exercise: bool
    min_vested_shares_to_exercise: int | None = None
    allowed_repayment_methods: list[LoanRepaymentMethod]
    min_loan_term_months: int
    max_loan_term_months: int
    allowed_interest_types: list[LoanInterestType]
    fixed_interest_rate_annual_percent: Decimal | None = None
    variable_base_rate_annual_percent: Decimal | None = None
    variable_margin_annual_percent: Decimal | None = None
    require_down_payment: bool
    down_payment_percent: Decimal | None = None


class OrgSettingsUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    allow_user_data_export: bool | None = None
    allow_profile_edit: bool | None = None
    require_two_factor: bool | None = None
    audit_log_retention_days: int | None = Field(default=None, ge=0)
    inactive_user_retention_days: int | None = Field(default=None, ge=0)
    enforce_service_duration_rule: bool | None = None
    min_service_duration_days: int | None = Field(default=None, ge=0)
    enforce_min_vested_to_exercise: bool | None = None
    min_vested_shares_to_exercise: int | None = Field(default=None, ge=0)
    allowed_repayment_methods: list[LoanRepaymentMethod] | None = None
    min_loan_term_months: int | None = Field(default=None, ge=1)
    max_loan_term_months: int | None = Field(default=None, ge=1)
    allowed_interest_types: list[LoanInterestType] | None = None
    fixed_interest_rate_annual_percent: Decimal | None = Field(default=None, ge=0, le=100)
    variable_base_rate_annual_percent: Decimal | None = Field(default=None, ge=0, le=100)
    variable_margin_annual_percent: Decimal | None = Field(default=None, ge=0, le=100)
    require_down_payment: bool | None = None
    down_payment_percent: Decimal | None = Field(default=None, ge=0, le=100)
