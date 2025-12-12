from datetime import datetime, date
from uuid import UUID
from pydantic import BaseModel, EmailStr


class UserSummary(BaseModel):
    id: UUID
    org_id: str
    email: EmailStr
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    preferred_name: str | None = None
    timezone: str | None = None
    phone_number: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class MembershipSummary(BaseModel):
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


class UserListItem(BaseModel):
    user: UserSummary
    membership: MembershipSummary


class UserListResponse(BaseModel):
    items: list[UserListItem]


class UserDetailResponse(UserListItem):
    pass


class UpdateMembershipRequest(BaseModel):
    employment_status: str | None = None
    platform_status: str | None = None
