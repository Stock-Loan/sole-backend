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
