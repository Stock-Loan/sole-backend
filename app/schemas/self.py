from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.common import MaritalStatus, normalize_marital_status


class OrgSummary(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class RoleSummary(BaseModel):
    id: UUID
    name: str
    is_system_role: bool
    permissions: list[str]

    class Config:
        from_attributes = True


class SelfContextResponse(BaseModel):
    org: OrgSummary
    roles: list[RoleSummary]
    permissions: list[str]
    session_timeout_minutes: int = 5
    tenancy_mode: Literal["single", "multi"] = "single"


class SelfProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

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
