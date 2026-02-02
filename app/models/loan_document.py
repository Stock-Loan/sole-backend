import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


DOCUMENT_TYPES = (
    "NOTICE_OF_STOCK_OPTION_GRANT",
    "PAYMENT_INSTRUCTIONS",
    "PAYMENT_CONFIRMATION",
    "STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT",
    "SECURED_PROMISSORY_NOTE",
    "SPOUSE_PARTNER_CONSENT",
    "STOCK_POWER_AND_ASSIGNMENT",
    "INVESTMENT_REPRESENTATION_STATEMENT",
    "SHARE_CERTIFICATE",
    "SECTION_83B_ELECTION",
)

STAGE_TYPES = (
    "HR_REVIEW",
    "FINANCE_PROCESSING",
    "LEGAL_EXECUTION",
    "LEGAL_POST_ISSUANCE",
    "BORROWER_83B_ELECTION",
)


class LoanDocument(Base):
    __tablename__ = "loan_documents"
    __allow_unmapped__ = True
    __table_args__ = (
        CheckConstraint(
            "stage_type IN ('HR_REVIEW', 'FINANCE_PROCESSING', 'LEGAL_EXECUTION', 'LEGAL_POST_ISSUANCE', 'BORROWER_83B_ELECTION')",
            name="ck_loan_document_stage_type",
        ),
        CheckConstraint(
            "document_type IN ('NOTICE_OF_STOCK_OPTION_GRANT', 'PAYMENT_INSTRUCTIONS', 'PAYMENT_CONFIRMATION', 'STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT', 'SECURED_PROMISSORY_NOTE', 'SPOUSE_PARTNER_CONSENT', 'STOCK_POWER_AND_ASSIGNMENT', 'INVESTMENT_REPRESENTATION_STATEMENT', 'SHARE_CERTIFICATE', 'SECTION_83B_ELECTION')",
            name="ck_loan_document_type",
        ),
        Index("ix_loan_documents_org_id", "org_id"),
        Index("ix_loan_documents_org_stage_type", "org_id", "stage_type"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    loan_application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("loan_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_type = Column(String(50), nullable=False)
    document_type = Column(String(100), nullable=False)
    file_name = Column(String(255), nullable=False)
    storage_path_or_url = Column(String(1024), nullable=False)
    storage_provider = Column(String(32), nullable=True)
    storage_bucket = Column(String(255), nullable=True)
    storage_object_key = Column(String(1024), nullable=True)
    content_type = Column(String(100), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    checksum = Column(String(128), nullable=True)
    uploaded_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    loan_application = relationship("LoanApplication", back_populates="documents")
    uploaded_by_user = relationship("User", foreign_keys=[uploaded_by_user_id])

    @property
    def uploaded_by_name(self) -> str | None:
        user = getattr(self, "uploaded_by_user", None)
        return user.full_name if user else None
