from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.core.permissions import PermissionCode


class RoleBase(BaseModel):
    name: str
    description: str | None = None
    permissions: list[str] = []

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        value = v.strip()
        if not value:
            raise ValueError("Role name cannot be empty")
        return value

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
    def strip_name(cls, v: str | None) -> str | None:
        return v.strip() if v else v

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

    class Config:
        from_attributes = True


class RoleListResponse(BaseModel):
    items: list[RoleOut]
    total: int


class RoleAssignmentRequest(BaseModel):
    role_ids: list[UUID]
