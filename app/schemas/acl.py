from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.core.permissions import PermissionCode


class ACLCreate(BaseModel):
    user_id: UUID
    resource_type: str
    resource_id: str
    permissions: list[str]

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

    @field_validator("resource_type", "resource_id")
    @classmethod
    def non_empty(cls, v: str) -> str:
        value = (v or "").strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class ACLOut(BaseModel):
    id: UUID
    org_id: str
    user_id: UUID
    resource_type: str
    resource_id: str
    permissions: list[str]
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class ACLListResponse(BaseModel):
    items: list[ACLOut]
