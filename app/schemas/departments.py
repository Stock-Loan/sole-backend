from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class DepartmentBase(BaseModel):
    name: str
    code: str
    is_archived: bool | None = None

    @field_validator("name", "code")
    @classmethod
    def strip_non_empty(cls, v: str) -> str:
        value = (v or "").strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value


class DepartmentCreate(DepartmentBase):
    is_archived: bool | None = False


class DepartmentUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    is_archived: bool | None = None

    @field_validator("name", "code")
    @classmethod
    def strip_opt(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class DepartmentOut(BaseModel):
    id: UUID
    org_id: str
    name: str
    code: str
    is_archived: bool
    member_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class DepartmentListResponse(BaseModel):
    items: list[DepartmentOut]
    total: int


class DepartmentAssignRequest(BaseModel):
    membership_ids: list[str]


class DepartmentAssignResponse(BaseModel):
    department: DepartmentOut
    assigned: list[str]
    skipped_inactive: list[str]
    not_found: list[str]
