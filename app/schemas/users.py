from datetime import datetime, date
from uuid import UUID
from pydantic import BaseModel, EmailStr, ConfigDict, field_validator

from app.schemas.common import (
    EmploymentStatus,
    MaritalStatus,
    normalize_employment_status,
    normalize_marital_status,
)


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    org_id: str | None = None
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
    mfa_enabled: bool = False
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

    email: EmailStr | None = None
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


class UserDepartmentCount(BaseModel):
    department_id: UUID
    department_name: str
    count: int


class UserRoleCount(BaseModel):
    role_id: UUID
    role_name: str
    count: int


class UserDashboardSummary(BaseModel):
    org_id: str
    total_users: int
    platform_status_counts: dict[str, int]
    invitation_status_counts: dict[str, int]
    employment_status_counts: dict[str, int]
    active_users: int
    suspended_users: int
    invited_pending: int
    accepted_invites: int
    mfa_enabled: int
    mfa_disabled: int
    never_logged_in: int
    active_last_7_days: int
    active_last_30_days: int
    stale_30_plus_days: int
    users_with_temp_password: int
    users_without_department: int
    missing_profile_fields: int
    department_counts: list[UserDepartmentCount]
    role_counts: list[UserRoleCount]
    roles_with_zero_members: list[str]
