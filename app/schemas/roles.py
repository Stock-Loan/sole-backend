from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.core.permissions import PermissionCode
from app.schemas.common import normalize_description_text, normalize_title_text


class RoleBase(BaseModel):
    name: str
    description: str | None = None
    permissions: list[str] = []

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        value = normalize_title_text(v)
        if not value:
            raise ValueError("Role name cannot be empty")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_text(v)

    @field_validator("permissions")
    @classmethod
    def normalize_permissions(cls, v: list[str]) -> list[str]:
        return PermissionCode.normalize(v or [])


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = normalize_title_text(v)
        if not normalized:
            raise ValueError("Role name cannot be empty")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_text(v)

    @field_validator("permissions")
    @classmethod
    def normalize_permissions(cls, v: list[str] | None) -> list[str] | None:
        return PermissionCode.normalize(v) if v is not None else None


class RoleOut(BaseModel):
    id: UUID
    org_id: str
    name: str
    description: str | None = None
    is_system_role: bool
    permissions: list[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_output_name(cls, v: str) -> str:
        return normalize_title_text(v) or v

    @field_validator("description", mode="before")
    @classmethod
    def normalize_output_description(cls, v: str | None) -> str | None:
        return normalize_description_text(v)

    class Config:
        from_attributes = True


class RoleListResponse(BaseModel):
    items: list[RoleOut]
    total: int


class RoleAssignmentRequest(BaseModel):
    role_ids: list[UUID]
