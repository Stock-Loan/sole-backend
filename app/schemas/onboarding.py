from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class OnboardingUserCreate(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    middle_name: str | None = None
    preferred_name: str | None = None
    timezone: str | None = None
    phone_number: str | None = None
    marital_status: str | None = None
    country: str | None = None
    state: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    temporary_password: str | None = None
    employee_id: str
    employment_start_date: date | None = None
    employment_status: str = "ACTIVE"


class OnboardingUserOut(BaseModel):
    id: UUID
    org_id: str
    email: EmailStr
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    preferred_name: str | None = None
    timezone: str | None = None
    phone_number: str | None = None
    marital_status: str | None = None
    country: str | None = None
    state: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class OnboardingMembershipOut(BaseModel):
    id: UUID
    org_id: str
    user_id: UUID
    employee_id: str
    employment_start_date: date | None = None
    employment_status: str
    platform_status: str
    invitation_status: str
    invited_at: datetime | None = None
    accepted_at: datetime | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class OnboardingResponse(BaseModel):
    user: OnboardingUserOut
    membership: OnboardingMembershipOut
    temporary_password: str | None = None


class BulkOnboardingRowSuccess(BaseModel):
    row_number: int
    user: OnboardingUserOut
    membership: OnboardingMembershipOut
    temporary_password: str | None = None


class BulkOnboardingRowError(BaseModel):
    row_number: int
    email: EmailStr | None = None
    employee_id: str | None = None
    error: str


class BulkOnboardingResult(BaseModel):
    successes: list[BulkOnboardingRowSuccess]
    errors: list[BulkOnboardingRowError]
