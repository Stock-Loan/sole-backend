from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict

from app.schemas.settings import LoanInterestType, LoanRepaymentMethod
from app.schemas.stock import EligibilityResult


class LoanSelectionMode(str, Enum):
    PERCENT = "PERCENT"
    SHARES = "SHARES"


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
