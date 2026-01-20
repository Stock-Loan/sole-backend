import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class UserMfaDevice(Base):
    __tablename__ = "user_mfa_devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    user_agent = Column(String(500), nullable=True)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
