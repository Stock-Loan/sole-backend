import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Identity(Base):
    __tablename__ = "identities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    mfa_enabled = Column(Boolean, nullable=False, server_default="false")
    mfa_method = Column(String(50), nullable=True)
    mfa_secret_encrypted = Column(String(255), nullable=True)
    mfa_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    token_version = Column(Integer, nullable=False, server_default="0")
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    must_change_password = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    users = relationship("User", back_populates="identity")
    recovery_codes = relationship(
        "UserMfaRecoveryCode", back_populates="identity", cascade="all, delete-orphan"
    )
