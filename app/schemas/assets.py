from uuid import UUID
from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict

class UploadSessionRequest(BaseModel):
    org_id: str
    kind: str = Field(..., description="org_template, display_image, loan_document, etc.")
    owner_refs: Dict[str, str] = Field(..., description="IDs needed to build the path: user_id, loan_id, etc.")
    filename: str
    content_type: str
    size_bytes: int
    checksum: str | None = None

class UploadSessionResponse(BaseModel):
    asset_id: UUID
    upload_url: str
    storage_provider: str
    storage_bucket: str | None = None
    object_key: str
    required_headers_or_fields: Dict[str, Any] = Field(default_factory=dict)

class AssetRead(BaseModel):
    id: UUID
    org_id: str
    owner_type: str
    owner_id: str
    kind: str
    filename: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    status: str
    provider: Optional[str] = None
    bucket: Optional[str] = None
    object_key: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
