import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class OrgDocumentFolder(Base):
    __tablename__ = "org_document_folders"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_org_document_folders_org_name"),
        UniqueConstraint("org_id", "system_key", name="uq_org_document_folders_org_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    system_key = Column(String(50), nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
