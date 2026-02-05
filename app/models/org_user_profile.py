import uuid

from sqlalchemy import Column, DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class OrgUserProfile(Base):
    __tablename__ = "org_user_profiles"
    __table_args__ = (
        UniqueConstraint("org_id", "membership_id", name="uq_org_user_profiles_org_membership"),
        ForeignKeyConstraint(
            ["org_id", "membership_id"],
            ["org_memberships.org_id", "org_memberships.id"],
            ondelete="CASCADE",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, nullable=False, index=True)
    membership_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    full_name = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=True)
    middle_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    preferred_name = Column(String(255), nullable=True)
    timezone = Column(String(50), nullable=True)
    phone_number = Column(String(50), nullable=True)
    marital_status = Column(String(50), nullable=True)
    country = Column(String(2), nullable=True)
    state = Column(String(10), nullable=True)
    address_line1 = Column(String(255), nullable=True)
    address_line2 = Column(String(255), nullable=True)
    postal_code = Column(String(32), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    membership = relationship("OrgMembership", back_populates="profile")
