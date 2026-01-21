from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


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
