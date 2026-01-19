import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class LoanRepayment(Base):
    __tablename__ = "loan_repayments"
    __allow_unmapped__ = True
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_loan_repayment_amount_nonneg"),
        CheckConstraint("principal_amount >= 0", name="ck_loan_repayment_principal_nonneg"),
        CheckConstraint("interest_amount >= 0", name="ck_loan_repayment_interest_nonneg"),
        Index("ix_loan_repayments_org_id", "org_id"),
        Index("ix_loan_repayments_org_loan", "org_id", "loan_application_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    loan_application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("loan_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount = Column(Numeric(18, 6), nullable=False)
    principal_amount = Column(Numeric(18, 6), nullable=False, default=0)
    interest_amount = Column(Numeric(18, 6), nullable=False, default=0)
    payment_date = Column(Date, nullable=False)
    recorded_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    evidence_file_name = Column(String(255), nullable=True)
    evidence_storage_path_or_url = Column(String(1024), nullable=True)
    evidence_content_type = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    loan_application = relationship("LoanApplication", back_populates="repayments")
    recorded_by_user = relationship("User", foreign_keys=[recorded_by_user_id])

    @property
    def recorded_by_name(self) -> str | None:
        user = getattr(self, "recorded_by_user", None)
        return user.full_name if user else None
