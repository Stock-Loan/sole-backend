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
    storage_provider: str | None = None
    storage_bucket: str | None = None
    storage_object_key: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    uploaded_by_name: str | None = None
    created_at: datetime | None = None


class OrgDocumentTemplateListResponse(BaseModel):
    items: list[OrgDocumentTemplateDTO]
    total: int


class OrgDocumentFolderListResponse(BaseModel):
    items: list[OrgDocumentFolderDTO]
    total: int


class OrgDocumentTemplateCreate(BaseModel):
    folder_id: UUID | None = None
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    file_name: str = Field(min_length=1, max_length=255)
    storage_key: str = Field(min_length=1)
    storage_provider: str | None = None
    storage_bucket: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None


class OrgDocumentTemplateUploadUrlRequest(BaseModel):
    folder_id: UUID | None = None
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str
    size_bytes: int
    checksum: str | None = None


class OrgDocumentTemplateUploadUrlResponse(BaseModel):
    upload_url: str
    required_headers_or_fields: dict[str, str]
    storage_provider: str
    storage_bucket: str | None
    storage_key: str
    file_name: str
