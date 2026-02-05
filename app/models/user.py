import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_users_org_email"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    is_superuser = Column(Boolean, nullable=False, server_default="false")
    mfa_enabled = Column(Boolean, nullable=False, server_default="false")
    mfa_method = Column(String(50), nullable=True)
    mfa_secret_encrypted = Column(String(255), nullable=True)
    mfa_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    token_version = Column(Integer, nullable=False, server_default="0")
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    must_change_password = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    memberships = relationship("OrgMembership", back_populates="user", cascade="all, delete-orphan")
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")

    @property
    def profile(self):
        memberships = getattr(self, "memberships", None) or []
        if memberships:
            return memberships[0].profile
        return None

    @property
    def full_name(self) -> str | None:
        profile = self.profile
        return profile.full_name if profile else None

    @property
    def first_name(self) -> str | None:
        profile = self.profile
        return profile.first_name if profile else None

    @property
    def middle_name(self) -> str | None:
        profile = self.profile
        return profile.middle_name if profile else None

    @property
    def last_name(self) -> str | None:
        profile = self.profile
        return profile.last_name if profile else None

    @property
    def preferred_name(self) -> str | None:
        profile = self.profile
        return profile.preferred_name if profile else None

    @property
    def timezone(self) -> str | None:
        profile = self.profile
        return profile.timezone if profile else None

    @property
    def phone_number(self) -> str | None:
        profile = self.profile
        return profile.phone_number if profile else None

    @property
    def marital_status(self) -> str | None:
        profile = self.profile
        return profile.marital_status if profile else None

    @property
    def country(self) -> str | None:
        profile = self.profile
        return profile.country if profile else None

    @property
    def state(self) -> str | None:
        profile = self.profile
        return profile.state if profile else None

    @property
    def address_line1(self) -> str | None:
        profile = self.profile
        return profile.address_line1 if profile else None

    @property
    def address_line2(self) -> str | None:
        profile = self.profile
        return profile.address_line2 if profile else None

    @property
    def postal_code(self) -> str | None:
        profile = self.profile
        return profile.postal_code if profile else None
