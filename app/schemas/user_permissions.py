from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.core.permissions import PermissionCode


class UserPermissionAssignmentCreate(BaseModel):
    user_id: UUID
    permissions: list[str]
    effect: Literal["allow", "deny"] = "allow"
    expires_at: datetime | None = None

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str]) -> list[str]:
        validated: list[str] = []
        for code in v or []:
            try:
                validated.append(PermissionCode(code).value)
            except ValueError:
                raise ValueError(f"Unknown permission: {code}")
        return validated


class UserPermissionAssignmentUpdate(BaseModel):
    permissions: list[str] | None = None
    effect: Literal["allow", "deny"] | None = None
    expires_at: datetime | None = None

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        validated: list[str] = []
        for code in v or []:
            try:
                validated.append(PermissionCode(code).value)
            except ValueError:
                raise ValueError(f"Unknown permission: {code}")
        return validated


class UserPermissionAssignmentOut(BaseModel):
    id: UUID
    org_id: str
    user_id: UUID
    full_name: str | None = None
    email: str | None = None
    permissions: list[str]
    effect: str
    expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class UserPermissionAssignmentList(BaseModel):
    items: list[UserPermissionAssignmentOut]
