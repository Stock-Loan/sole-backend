from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Numeric, func, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class OrgSettings(Base):
    __tablename__ = "org_settings"
    __allow_unmapped__ = True

    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True)
    allow_user_data_export = Column(Boolean, nullable=False, default=True, server_default="true")
    allow_profile_edit = Column(Boolean, nullable=False, default=True, server_default="true")
    require_two_factor = Column(Boolean, nullable=False, default=False, server_default="false")
    remember_device_days = Column(Integer, nullable=False, default=30, server_default="30")
    audit_log_retention_days = Column(Integer, nullable=False, default=180, server_default="180")
    inactive_user_retention_days = Column(Integer, nullable=False, default=180, server_default="180")
    enforce_service_duration_rule = Column(Boolean, nullable=False, default=False, server_default="false")
    min_service_duration_years = Column(Numeric(6, 2), nullable=True)
    enforce_min_vested_to_exercise = Column(Boolean, nullable=False, default=False, server_default="false")
    min_vested_shares_to_exercise = Column(BigInteger, nullable=True)
    allowed_repayment_methods = Column(
        JSONB,
        nullable=False,
        default=lambda: ["INTEREST_ONLY", "BALLOON", "PRINCIPAL_AND_INTEREST"],
        server_default=text(
            "'[\"INTEREST_ONLY\", \"BALLOON\", \"PRINCIPAL_AND_INTEREST\"]'::jsonb"
        ),
    )
    min_loan_term_months = Column(Integer, nullable=False, default=6, server_default="6")
    max_loan_term_months = Column(Integer, nullable=False, default=60, server_default="60")
    allowed_interest_types = Column(
        JSONB,
        nullable=False,
        default=lambda: ["FIXED", "VARIABLE"],
        server_default=text("'[\"FIXED\", \"VARIABLE\"]'::jsonb"),
    )
    fixed_interest_rate_annual_percent = Column(
        Numeric(10, 4), nullable=False, default=0, server_default="0"
    )
    variable_base_rate_annual_percent = Column(Numeric(10, 4), nullable=True)
    variable_margin_annual_percent = Column(Numeric(10, 4), nullable=True)
    require_down_payment = Column(Boolean, nullable=False, default=False, server_default="false")
    down_payment_percent = Column(Numeric(5, 2), nullable=True)
    policy_version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
