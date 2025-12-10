import uuid

from sqlalchemy import JSON, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"postgresql_partition_by": "LIST (org_id)"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, primary_key=True, nullable=False, index=True)
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    action = Column(String(255), nullable=False)
    resource_type = Column(String(255), nullable=False)
    resource_id = Column(String(255), nullable=False)
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
