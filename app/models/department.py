import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint, func, Boolean
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class Department(Base):
    __tablename__ = "departments"
    __allow_unmapped__ = True
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_departments_org_name"),
        UniqueConstraint("org_id", "code", name="uq_departments_org_code"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=False)
    is_archived = Column(Boolean, nullable=False, default=False)
    # transient field for response use (member_count)
    member_count: int | None = None  # transient, not mapped
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
