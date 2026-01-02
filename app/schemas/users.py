from datetime import datetime, date
from uuid import UUID
from pydantic import BaseModel, EmailStr, ConfigDict, field_validator

from app.schemas.common import EmploymentStatus, MaritalStatus, normalize_employment_status, normalize_marital_status


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    org_id: str
    email: EmailStr
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    preferred_name: str | None = None
    timezone: str | None = None
    phone_number: str | None = None
    marital_status: MaritalStatus | None = None
    country: str | None = None
    state: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: datetime | None = None


class MembershipSummary(BaseModel):
    id: UUID
    org_id: str
    user_id: UUID
    employee_id: str
    department_id: UUID | None = None
    department_name: str | None = None
    employment_start_date: date | None = None
    employment_status: str
    platform_status: str
    invitation_status: str
    invited_at: datetime | None = None
    accepted_at: datetime | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class RoleSummary(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    is_system_role: bool

    class Config:
        from_attributes = True


class UserListItem(BaseModel):
    user: UserSummary
    membership: MembershipSummary
    roles: list[RoleSummary] = []


class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: int


class UserDetailResponse(UserListItem):
    pass


class UpdateMembershipRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    employment_status: EmploymentStatus | None = None
    platform_status: str | None = None

    @field_validator("employment_status", mode="before")
    @classmethod
    def _normalize_employment_status(cls, value):
        return normalize_employment_status(value) if value is not None else None


class UpdateUserProfileRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    preferred_name: str | None = None
    timezone: str | None = None
    phone_number: str | None = None
    marital_status: MaritalStatus | None = None
    country: str | None = None
    state: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None

    @field_validator("marital_status", mode="before")
    @classmethod
    def _normalize_marital_status(cls, value):
        return normalize_marital_status(value)


class BulkDeleteRequest(BaseModel):
    membership_ids: list[str]
