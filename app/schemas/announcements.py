from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

ALLOWED_STATUSES = {"DRAFT", "PUBLISHED", "UNPUBLISHED", "ARCHIVED"}
ALLOWED_TYPES = {"GENERAL", "MAINTENANCE", "OUTAGE", "POLICY", "FEATURE"}


class AnnouncementBase(BaseModel):
    title: str
    body: str
    status: str | None = None
    type: str | None = None
    scheduled_at: datetime | None = None

    @field_validator("title", "body")
    @classmethod
    def non_empty(cls, v: str) -> str:
        value = (v or "").strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.strip().upper()
        if normalized not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid status. Allowed: {sorted(ALLOWED_STATUSES)}")
        return normalized

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.strip().upper()
        if normalized not in ALLOWED_TYPES:
            raise ValueError(f"Invalid type. Allowed: {sorted(ALLOWED_TYPES)}")
        return normalized


class AnnouncementCreate(AnnouncementBase):
    status: str | None = "DRAFT"


class AnnouncementUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    status: str | None = None
    type: str | None = None
    scheduled_at: datetime | None = None

    @field_validator("title", "body")
    @classmethod
    def strip_opt(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.strip().upper()
        if normalized not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid status. Allowed: {sorted(ALLOWED_STATUSES)}")
        return normalized

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.strip().upper()
        if normalized not in ALLOWED_TYPES:
            raise ValueError(f"Invalid type. Allowed: {sorted(ALLOWED_TYPES)}")
        return normalized


class AnnouncementOut(BaseModel):
    id: UUID
    org_id: str
    title: str
    body: str
    status: str
    type: str
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    read_count: int | None = 0
    target_count: int | None = None

    class Config:
        from_attributes = True


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementOut]
    total: int
