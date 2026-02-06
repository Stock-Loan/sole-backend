from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.loan import LoanWorkflowStageStatus, LoanWorkflowStageType
from app.schemas.stock import (
    EligibilityResult,
    GrantSummary,
    NextVestingEvent,
    StockPolicySnapshot,
    StockReservationSummary,
)


class PendingAction(BaseModel):
    action_type: str
    label: str
    due_date: date | None = None
    related_id: UUID | None = None


class SelfDashboardAttention(BaseModel):
    unread_announcements_count: int
    pending_actions_count: int
    pending_actions: list[PendingAction] = Field(default_factory=list)


class SelfProfileCompletion(BaseModel):
    completion_percent: int
    missing_fields: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    total_required_fields: int
    missing_count: int
    is_complete: bool


class SelfStockTotals(BaseModel):
    grant_count: int
    total_granted_shares: int
    total_vested_shares: int
    total_unvested_shares: int
    total_reserved_shares: int
    total_available_vested_shares: int
    exercise_price_min: Decimal | None = None
    exercise_price_max: Decimal | None = None
    weighted_avg_exercise_price: Decimal | None = None

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class VestedByMonth(BaseModel):
    month: str
    shares: int


class SelfVestingTimeline(BaseModel):
    next_vesting_date: date | None = None
    next_vesting_shares: int | None = None
    upcoming_events: list[NextVestingEvent] = Field(default_factory=list)
    vested_by_month: list[VestedByMonth] = Field(default_factory=list)


class SelfGrantMix(BaseModel):
    by_status: dict[str, int] = Field(default_factory=dict)
    by_vesting_strategy: dict[str, int] = Field(default_factory=dict)


class SelfStockReservations(BaseModel):
    reserved_share_percent_of_vested: Decimal = Decimal("0")
    reserved_by_status: dict[str, int] = Field(default_factory=dict)
    reservations_active: list[StockReservationSummary] = Field(default_factory=list)

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class SelfGrantSummary(GrantSummary):
    next_vesting_date: date | None = None
    next_vesting_shares: int | None = None

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class SelfLoanSummary(BaseModel):
    total_loan_applications: int
    active_loans_count: int
    completed_loans_count: int
    pending_loans_count: int
    active_loan_id: UUID | None = None
    status: str | None = None
    principal: Decimal | None = None
    estimated_monthly_payment: Decimal | None = None
    total_payable: Decimal | None = None
    total_paid: Decimal | None = None
    total_interest_paid: Decimal | None = None
    remaining_balance: Decimal | None = None
    principal_remaining: Decimal | None = None
    interest_remaining: Decimal | None = None
    total_remaining: Decimal | None = None
    next_payment_date: date | None = None
    next_payment_amount: Decimal | None = None
    missed_payment_count: int | None = None
    missed_payment_amount_total: Decimal | None = None
    missed_payment_dates: list[date] = Field(default_factory=list)
    current_stage_type: LoanWorkflowStageType | None = None
    current_stage_status: LoanWorkflowStageStatus | None = None
    has_share_certificate: bool | None = None
    has_83b_election: bool | None = None
    days_until_83b_due: int | None = None

    model_config = ConfigDict(
        json_encoders={Decimal: lambda value: str(value)}, use_enum_values=True
    )


class RepaymentHistoryItem(BaseModel):
    payment_date: date
    amount: Decimal

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class SelfLoanRepaymentActivity(BaseModel):
    last_payment_date: date | None = None
    last_payment_amount: Decimal | None = None
    repayment_history: list[RepaymentHistoryItem] = Field(default_factory=list)

    model_config = ConfigDict(json_encoders={Decimal: lambda value: str(value)})


class SelfDashboardSummary(BaseModel):
    as_of_date: date
    attention: SelfDashboardAttention
    profile_completion: SelfProfileCompletion
    stock_totals: SelfStockTotals
    stock_eligibility: EligibilityResult
    vesting_timeline: SelfVestingTimeline
    grant_mix: SelfGrantMix
    reservations: SelfStockReservations
    grants: list[SelfGrantSummary] = Field(default_factory=list)
    grants_total: int
    policy_snapshot: StockPolicySnapshot
    loan_summary: SelfLoanSummary
    repayment_activity: SelfLoanRepaymentActivity

    model_config = ConfigDict(use_enum_values=True)
