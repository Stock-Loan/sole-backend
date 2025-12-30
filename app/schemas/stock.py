from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class VestingStrategy(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    SCHEDULED = "SCHEDULED"


class StockGrantStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    EXERCISED_OUT = "EXERCISED_OUT"


class EligibilityReasonCode(str, Enum):
    EMPLOYMENT_INACTIVE = "EMPLOYMENT_INACTIVE"
    INSUFFICIENT_SERVICE_DURATION = "INSUFFICIENT_SERVICE_DURATION"
    NO_VESTED_SHARES = "NO_VESTED_SHARES"
    BELOW_MIN_VESTED_THRESHOLD = "BELOW_MIN_VESTED_THRESHOLD"


class EligibilityReason(BaseModel):
    code: EligibilityReasonCode
    message: str


class EligibilityResult(BaseModel):
    eligible_to_exercise: bool
    total_granted_shares: int
    total_vested_shares: int
    total_unvested_shares: int
    reasons: list[EligibilityReason] = Field(default_factory=list)


class VestingEventBase(BaseModel):
    vest_date: date
    shares: int = Field(ge=0)


class VestingEventCreate(VestingEventBase):
    pass


class VestingEventOut(VestingEventBase):
    id: UUID

    class Config:
        from_attributes = True


class EmployeeStockGrantBase(BaseModel):
    grant_date: date
    total_shares: int = Field(ge=0)
    exercise_price: Decimal = Field(ge=0)
    vesting_strategy: VestingStrategy
    notes: str | None = None


class EmployeeStockGrantCreate(EmployeeStockGrantBase):
    vesting_events: list[VestingEventCreate] = Field(default_factory=list)


class EmployeeStockGrantUpdate(BaseModel):
    grant_date: date | None = None
    total_shares: int | None = Field(default=None, ge=0)
    exercise_price: Decimal | None = Field(default=None, ge=0)
    status: StockGrantStatus | None = None
    vesting_strategy: VestingStrategy | None = None
    notes: str | None = None
    vesting_events: list[VestingEventCreate] | None = None


class EmployeeStockGrantOut(EmployeeStockGrantBase):
    id: UUID
    org_id: str
    org_membership_id: UUID
    status: StockGrantStatus
    vesting_events: list[VestingEventOut] = Field(default_factory=list)
    vested_shares: int = 0
    unvested_shares: int = 0

    class Config:
        from_attributes = True


class StockGrantListResponse(BaseModel):
    items: list[EmployeeStockGrantOut]
    total: int


class GrantSummary(BaseModel):
    grant_id: UUID
    grant_date: date
    total_shares: int
    vested_shares: int
    unvested_shares: int
    exercise_price: Decimal


class NextVestingEvent(BaseModel):
    vest_date: date
    shares: int


class StockSummaryResponse(BaseModel):
    org_membership_id: UUID
    total_granted_shares: int
    total_vested_shares: int
    total_unvested_shares: int
    next_vesting_event: NextVestingEvent | None = None
    eligibility_result: EligibilityResult
    grants: list[GrantSummary] = Field(default_factory=list)
