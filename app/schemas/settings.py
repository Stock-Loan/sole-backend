from datetime import datetime

from pydantic import BaseModel, Field


class OrgSettingsBase(BaseModel):
    allow_user_data_export: bool = Field(default=True)
    allow_profile_edit: bool = Field(default=True)
    require_two_factor: bool = Field(default=False)
    audit_log_retention_days: int = Field(default=180, ge=0)
    inactive_user_retention_days: int = Field(default=180, ge=0)


class OrgSettingsResponse(OrgSettingsBase):
    org_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class OrgSettingsUpdate(BaseModel):
    allow_user_data_export: bool | None = None
    allow_profile_edit: bool | None = None
    require_two_factor: bool | None = None
    audit_log_retention_days: int | None = Field(default=None, ge=0)
    inactive_user_retention_days: int | None = Field(default=None, ge=0)
