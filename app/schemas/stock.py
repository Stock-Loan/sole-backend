from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict, field_validator


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

    model_config = ConfigDict(from_attributes=True)


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
    next_vesting_event: NextVestingEvent | None = None
    next_vesting_summary: str | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: lambda value: str(value)},
    )


class StockGrantListResponse(BaseModel):
    items: list[EmployeeStockGrantOut]
    total: int


class GrantSummary(BaseModel):
    grant_id: UUID
    grant_date: date
    total_shares: int
    vested_shares: int
    unvested_shares: int
    reserved_shares: int = 0
    available_vested_shares: int = 0
    exercise_price: Decimal
    vesting_strategy: VestingStrategy | None = None
    status: StockGrantStatus | None = None

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class NextVestingEvent(BaseModel):
    vest_date: date
    shares: int


class StockReservationSummary(BaseModel):
    reservation_id: UUID
    loan_application_id: UUID
    grant_id: UUID
    shares_reserved: int
    status: str
    created_at: datetime | None = None


class StockPolicySnapshot(BaseModel):
    min_vested_shares_to_exercise: int | None = None
    enforce_min_vested_to_exercise: bool
    min_service_duration_years: Decimal | None = None
    enforce_service_duration_rule: bool

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class StockMembershipSnapshot(BaseModel):
    employment_status: str | None = None
    platform_status: str | None = None
    employment_start_date: date | None = None


class StockGrantPreviewResponse(BaseModel):
    grant_date: date
    total_shares: int
    exercise_price: Decimal
    vesting_strategy: VestingStrategy
    notes: str | None = None
    vesting_events: list[VestingEventBase] = Field(default_factory=list)
    next_vesting_event: NextVestingEvent | None = None
    next_vesting_summary: str | None = None

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class StockSummaryResponse(BaseModel):
    as_of_date: date
    org_membership_id: UUID
    grant_count: int
    total_granted_shares: int
    total_vested_shares: int
    total_unvested_shares: int
    total_reserved_shares: int = 0
    total_available_vested_shares: int = 0
    next_vesting_event: NextVestingEvent | None = None
    next_vesting_events: list[NextVestingEvent] = Field(default_factory=list)
    eligibility_result: EligibilityResult
    policy_snapshot: StockPolicySnapshot
    membership_snapshot: StockMembershipSnapshot
    exercise_price_min: Decimal | None = None
    exercise_price_max: Decimal | None = None
    active_reservations: list[StockReservationSummary] = Field(default_factory=list)
    grants: list[GrantSummary] = Field(default_factory=list)

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class StockDashboardTotals(BaseModel):
    program_employees: int
    grant_count: int
    total_granted_shares: int
    total_vested_shares: int
    total_unvested_shares: int
    total_reserved_shares: int
    total_available_vested_shares: int


class StockDashboardEligibility(BaseModel):
    eligible_to_exercise_count: int
    not_eligible_due_to_service_count: int
    not_eligible_due_to_min_vested_count: int
    not_eligible_due_to_other_count: int


class StockDashboardVestingTimeline(BaseModel):
    next_vesting_date: date | None = None
    next_vesting_shares: int | None = None
    upcoming_events: list[NextVestingEvent] = Field(default_factory=list)


class StockDashboardGrantMix(BaseModel):
    by_status: dict[str, int]
    by_vesting_strategy: dict[str, int]


class StockDashboardExercisePriceRange(BaseModel):
    min: Decimal | None = None
    max: Decimal | None = None

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class StockDashboardReservationPressure(BaseModel):
    reserved_share_percent_of_vested: Decimal
    reserved_by_status: dict[str, int]

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class StockDashboardMembershipSnapshot(BaseModel):
    by_platform_status: dict[str, int]
    by_employment_status: dict[str, int]


class StockDashboardSummary(BaseModel):
    org_id: str
    as_of: date
    totals: StockDashboardTotals
    eligibility: StockDashboardEligibility
    vesting_timeline: StockDashboardVestingTimeline
    grant_mix: StockDashboardGrantMix
    exercise_price_range: StockDashboardExercisePriceRange
    reservation_pressure: StockDashboardReservationPressure
    membership_snapshot: StockDashboardMembershipSnapshot
