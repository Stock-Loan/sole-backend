from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrgDocumentFolderDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: str
    name: str
    system_key: str | None = None
    is_system: bool
    template_count: int = 0
    created_at: datetime | None = None


class OrgDocumentFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrgDocumentFolderUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrgDocumentTemplateDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: str
    folder_id: UUID | None = None
    name: str
    description: str | None = None
    file_name: str
    storage_path_or_url: str
    uploaded_by_user_id: UUID | None = None
    created_at: datetime | None = None


class OrgDocumentTemplateListResponse(BaseModel):
    items: list[OrgDocumentTemplateDTO]
    total: int


class OrgDocumentFolderListResponse(BaseModel):
    items: list[OrgDocumentFolderDTO]
    total: int
