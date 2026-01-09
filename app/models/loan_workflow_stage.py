import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


STAGE_TYPES = (
    "HR_REVIEW",
    "FINANCE_PROCESSING",
    "LEGAL_EXECUTION",
    "LEGAL_POST_ISSUANCE",
    "BORROWER_83B_ELECTION",
)

STAGE_STATUSES = (
    "PENDING",
    "IN_PROGRESS",
    "COMPLETED",
)


class LoanWorkflowStage(Base):
    __tablename__ = "loan_workflow_stages"
    __allow_unmapped__ = True
    __table_args__ = (
        CheckConstraint(
            "stage_type IN ('HR_REVIEW', 'FINANCE_PROCESSING', 'LEGAL_EXECUTION', 'LEGAL_POST_ISSUANCE', 'BORROWER_83B_ELECTION')",
            name="ck_loan_workflow_stage_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED')",
            name="ck_loan_workflow_stage_status",
        ),
        Index("ix_loan_workflow_stages_org_id", "org_id"),
        Index("ix_loan_workflow_stages_org_stage_status", "org_id", "stage_type", "status"),
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
    status = Column(String(20), nullable=False, default="PENDING")
    assigned_role_hint = Column(String(50), nullable=True)
    completed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    loan_application = relationship("LoanApplication", back_populates="workflow_stages")
