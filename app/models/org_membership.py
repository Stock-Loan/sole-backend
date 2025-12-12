import uuid
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class OrgMembership(Base):
    __tablename__ = "org_memberships"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_membership_org_user"),
        UniqueConstraint("org_id", "employee_id", name="uq_membership_org_employee"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(String(255), nullable=False)
    employment_start_date = Column(Date, nullable=True)
    employment_status = Column(String(50), nullable=False, default="ACTIVE")
    platform_status = Column(String(50), nullable=False, default="INVITED")
    invitation_status = Column(String(50), nullable=False, default="PENDING")
    invited_at = Column(DateTime(timezone=True), nullable=True, default=func.now())
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
