import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("org_id", "email", name="uq_users_org_email"),
        UniqueConstraint("org_id", "identity_id", name="uq_users_org_identity"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    identity_id = Column(
        UUID(as_uuid=True), ForeignKey("identities.id"), nullable=False, index=True
    )
    email = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    is_superuser = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    identity = relationship("Identity", back_populates="users")
    memberships = relationship("OrgMembership", back_populates="user", cascade="all, delete-orphan")
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")

    _PROFILE_PROXY_FIELDS = {
        "full_name",
        "first_name",
        "middle_name",
        "last_name",
        "preferred_name",
        "timezone",
        "phone_number",
        "marital_status",
        "country",
        "state",
        "address_line1",
        "address_line2",
        "postal_code",
    }

    @property
    def profile(self):
        memberships = self.__dict__.get("memberships") or []
        if memberships:
            first_membership = memberships[0]
            return first_membership.__dict__.get("profile")
        return None

    def __getattr__(self, item: str):
        if item in self._PROFILE_PROXY_FIELDS:
            profile = self.profile
            if profile is None:
                return None
            return getattr(profile, item, None)
        raise AttributeError(f"{type(self).__name__!s} object has no attribute {item!r}")
