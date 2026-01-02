import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class LoanApplication(Base):
    __tablename__ = "loan_applications"
    __allow_unmapped__ = True
    __table_args__ = (
        CheckConstraint("shares_to_exercise >= 0", name="ck_loan_app_shares_nonneg"),
        CheckConstraint(
            "selection_value_snapshot >= 0",
            name="ck_loan_app_selection_value_nonneg",
        ),
        CheckConstraint(
            "total_exercisable_shares_snapshot >= 0",
            name="ck_loan_app_total_exercisable_nonneg",
        ),
        CheckConstraint("purchase_price >= 0", name="ck_loan_app_purchase_nonneg"),
        CheckConstraint("down_payment_amount >= 0", name="ck_loan_app_down_payment_nonneg"),
        CheckConstraint("loan_principal >= 0", name="ck_loan_app_principal_nonneg"),
        CheckConstraint("nominal_annual_rate_percent >= 0", name="ck_loan_app_rate_nonneg"),
        CheckConstraint("estimated_monthly_payment >= 0", name="ck_loan_app_payment_nonneg"),
        CheckConstraint("total_payable_amount >= 0", name="ck_loan_app_total_payable_nonneg"),
        CheckConstraint("total_interest_amount >= 0", name="ck_loan_app_total_interest_nonneg"),
        CheckConstraint("version >= 1", name="ck_loan_app_version_positive"),
        CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'CANCELLED')",
            name="ck_loan_app_status",
        ),
        CheckConstraint(
            "selection_mode IN ('PERCENT', 'SHARES')",
            name="ck_loan_app_selection_mode",
        ),
        CheckConstraint(
            "allocation_strategy IN ('OLDEST_VESTED_FIRST')",
            name="ck_loan_app_allocation_strategy",
        ),
        CheckConstraint(
            "interest_type IN ('FIXED', 'VARIABLE')",
            name="ck_loan_app_interest_type",
        ),
        CheckConstraint(
            "repayment_method IN ('INTEREST_ONLY', 'BALLOON', 'PRINCIPAL_AND_INTEREST')",
            name="ck_loan_app_repayment_method",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    org_membership_id = Column(
        UUID(as_uuid=True),
        ForeignKey("org_memberships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(30), nullable=False, default="DRAFT", index=True)
    version = Column(Integer, nullable=False, default=1)
    create_idempotency_key = Column(String(100), nullable=True)
    submit_idempotency_key = Column(String(100), nullable=True)
    as_of_date = Column(Date, nullable=False)
    selection_mode = Column(String(20), nullable=False)
    selection_value_snapshot = Column(Numeric(18, 6), nullable=False, default=0)
    shares_to_exercise = Column(BigInteger, nullable=False)
    total_exercisable_shares_snapshot = Column(BigInteger, nullable=False)
    purchase_price = Column(Numeric(18, 6), nullable=False)
    down_payment_amount = Column(Numeric(18, 6), nullable=False)
    loan_principal = Column(Numeric(18, 6), nullable=False)
    interest_type = Column(String(20), nullable=False)
    repayment_method = Column(String(40), nullable=False)
    term_months = Column(Integer, nullable=False)
    nominal_annual_rate_percent = Column(Numeric(10, 4), nullable=False)
    estimated_monthly_payment = Column(Numeric(18, 6), nullable=False)
    total_payable_amount = Column(Numeric(18, 6), nullable=False)
    total_interest_amount = Column(Numeric(18, 6), nullable=False)
    quote_inputs_snapshot = Column(JSONB, nullable=False, default=dict)
    quote_option_snapshot = Column(JSONB, nullable=False, default=dict)
    allocation_strategy = Column(String(50), nullable=False, default="OLDEST_VESTED_FIRST")
    allocation_snapshot = Column(JSONB, nullable=False, default=list)
    org_settings_snapshot = Column(JSONB, nullable=False, default=dict)
    eligibility_result_snapshot = Column(JSONB, nullable=False, default=dict)
    marital_status_snapshot = Column(String(50), nullable=True)
    spouse_first_name = Column(String(100), nullable=True)
    spouse_middle_name = Column(String(100), nullable=True)
    spouse_last_name = Column(String(100), nullable=True)
    spouse_email = Column(String(255), nullable=True)
    spouse_phone = Column(String(50), nullable=True)
    spouse_address = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __mapper_args__ = {"version_id_col": version}
