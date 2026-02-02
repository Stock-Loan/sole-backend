import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, BigInteger, func
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base


class Asset(Base):
    __tablename__ = "assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, nullable=False, index=True)

    owner_type = Column(String, nullable=False)  # org, user, loan, repayment
    owner_id = Column(String, nullable=False, index=True)

    kind = Column(String, nullable=False)  # org_template, display_image, loan_document, etc.

    content_type = Column(String, nullable=True)
    filename = Column(String, nullable=False)
    size_bytes = Column(BigInteger, nullable=True)
    checksum = Column(String, nullable=True)

    status = Column(String, nullable=False, default="pending")  # pending, uploaded, deleted

    storage_backend_id = Column(
        UUID(as_uuid=True), ForeignKey("storage_backend_configs.id"), nullable=True
    )
    provider = Column(String(32), nullable=True)
    bucket = Column(String, nullable=True)
    object_key = Column(String, nullable=False)
    external_url = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
