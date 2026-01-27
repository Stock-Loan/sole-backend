import uuid
from sqlalchemy import Column, String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db.base import Base


class StorageBackendConfig(Base):
    __tablename__ = "storage_backend_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, nullable=True, index=True)  # Null means system default
    provider = Column(String, nullable=False)  # s3_compatible, gcs, azure_blob, local
    bucket = Column(String, nullable=False)
    endpoint_url = Column(String, nullable=True)
    base_prefix = Column(String, nullable=True)
    credentials_ref = Column(String, nullable=True)  # Reference to secret, not raw
    configuration = Column(JSONB, nullable=False, default=dict)  # Extra config

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
