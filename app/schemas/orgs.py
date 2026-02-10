from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.tenant import normalize_org_id


class OrgCreateRequest(BaseModel):
    org_id: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=2, max_length=255)

    @field_validator("org_id", "slug", mode="before")
    @classmethod
    def _normalize_slug(cls, value):
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned.lower()

    @field_validator("org_id")
    @classmethod
    def _validate_org_id(cls, value: str) -> str:
        return normalize_org_id(value)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value):
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned


class OrgDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    status: str
    created_at: datetime
    updated_at: datetime
