from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.schemas.common import normalize_department_code, normalize_title_text


class DepartmentBase(BaseModel):
    name: str
    code: str
    is_archived: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        value = normalize_title_text(v)
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        value = normalize_department_code(v)
        if not value:
            raise ValueError("Value cannot be empty")
        return value


class DepartmentCreate(DepartmentBase):
    is_archived: bool | None = False


class DepartmentUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    is_archived: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = normalize_title_text(v)
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = normalize_department_code(v)
        if not value:
            raise ValueError("Value cannot be empty")
        return value


class DepartmentOut(BaseModel):
    id: UUID
    org_id: str
    name: str
    code: str
    is_archived: bool
    member_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_output_name(cls, v: str) -> str:
        return normalize_title_text(v) or v

    @field_validator("code", mode="before")
    @classmethod
    def normalize_output_code(cls, v: str) -> str:
        return normalize_department_code(v) or v

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
