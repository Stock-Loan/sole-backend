import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class OrgDocumentTemplate(Base):
    __tablename__ = "org_document_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    folder_id = Column(
        UUID(as_uuid=True),
        ForeignKey("org_document_folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    file_name = Column(String(255), nullable=False)
    storage_path_or_url = Column(String(1024), nullable=False)
    uploaded_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    uploaded_by_user = relationship("User", foreign_keys=[uploaded_by_user_id])

    @property
    def uploaded_by_name(self) -> str | None:
        user = getattr(self, "uploaded_by_user", None)
        return user.full_name if user else None
