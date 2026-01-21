import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class UserMfaRecoveryCode(Base):
    """Stores hashed one-time recovery codes for MFA backup."""
    __tablename__ = "user_mfa_recovery_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    used_at = Column(DateTime(timezone=True), nullable=True)
