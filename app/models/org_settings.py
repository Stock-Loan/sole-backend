from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func

from app.db.base import Base


class OrgSettings(Base):
    __tablename__ = "org_settings"
    __allow_unmapped__ = True

    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True)
    allow_user_data_export = Column(Boolean, nullable=False, default=True, server_default="true")
    allow_profile_edit = Column(Boolean, nullable=False, default=True, server_default="true")
    require_two_factor = Column(Boolean, nullable=False, default=False, server_default="false")
    audit_log_retention_days = Column(Integer, nullable=False, default=180, server_default="180")
    inactive_user_retention_days = Column(Integer, nullable=False, default=180, server_default="180")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
