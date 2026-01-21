import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class Announcement(Base):
    __tablename__ = "announcements"
    __allow_unmapped__ = True
    __table_args__ = (
        UniqueConstraint("org_id", "title", name="uq_announcements_org_title"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String(50), nullable=False, default="DRAFT")
    type = Column(String(50), nullable=False, default="GENERAL")
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    # transient fields for responses
    read_count: int | None = None
    target_count: int | None = None


class AnnouncementRead(Base):
    __tablename__ = "announcement_reads"
    __allow_unmapped__ = True
    __table_args__ = (
        UniqueConstraint("announcement_id", "user_id", name="uq_announcement_read"),
        Index("ix_announcement_reads_org_id", "org_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    announcement_id = Column(UUID(as_uuid=True), ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
