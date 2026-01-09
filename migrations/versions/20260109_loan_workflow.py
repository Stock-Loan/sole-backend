"""add loan workflow stages and documents

Revision ID: 20260109_loan_workflow
Revises: 20251231_loan_app_meta
Create Date: 2026-01-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260109_loan_workflow"
down_revision = "20251231_loan_app_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_loan_app_status", "loan_applications", type_="check")
    op.create_check_constraint(
        "ck_loan_app_status",
        "loan_applications",
        "status IN ('DRAFT', 'SUBMITTED', 'CANCELLED', 'IN_REVIEW', 'ACTIVE', 'REJECTED')",
    )

    op.create_table(
        "loan_workflow_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("loan_application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("assigned_role_hint", sa.String(length=50), nullable=True),
        sa.Column("completed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["completed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["loan_application_id"], ["loan_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "stage_type IN ('HR_REVIEW', 'FINANCE_PROCESSING', 'LEGAL_EXECUTION', 'LEGAL_POST_ISSUANCE', 'BORROWER_83B_ELECTION')",
            name="ck_loan_workflow_stage_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED')",
            name="ck_loan_workflow_stage_status",
        ),
    )
    op.create_index("ix_loan_workflow_stages_org_id", "loan_workflow_stages", ["org_id"], unique=False)
    op.create_index(
        "ix_loan_workflow_stages_org_stage_status",
        "loan_workflow_stages",
        ["org_id", "stage_type", "status"],
        unique=False,
    )
    op.create_index(
        "ix_loan_workflow_stages_loan_application_id",
        "loan_workflow_stages",
        ["loan_application_id"],
        unique=False,
    )

    op.create_table(
        "loan_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("loan_application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_type", sa.String(length=50), nullable=False),
        sa.Column("document_type", sa.String(length=100), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path_or_url", sa.String(length=1024), nullable=False),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["loan_application_id"], ["loan_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "stage_type IN ('HR_REVIEW', 'FINANCE_PROCESSING', 'LEGAL_EXECUTION', 'LEGAL_POST_ISSUANCE', 'BORROWER_83B_ELECTION')",
            name="ck_loan_document_stage_type",
        ),
        sa.CheckConstraint(
            "document_type IN ('NOTICE_OF_STOCK_OPTION_GRANT', 'PAYMENT_INSTRUCTIONS', 'PAYMENT_CONFIRMATION', 'STOCK_OPTION_EXERCISE_AND_LOAN_AGREEMENT', 'SECURED_PROMISSORY_NOTE', 'SPOUSE_PARTNER_CONSENT', 'STOCK_POWER_AND_ASSIGNMENT', 'INVESTMENT_REPRESENTATION_STATEMENT', 'SHARE_CERTIFICATE', 'SECTION_83B_ELECTION')",
            name="ck_loan_document_type",
        ),
    )
    op.create_index("ix_loan_documents_org_id", "loan_documents", ["org_id"], unique=False)
    op.create_index(
        "ix_loan_documents_org_stage_type",
        "loan_documents",
        ["org_id", "stage_type"],
        unique=False,
    )
    op.create_index(
        "ix_loan_documents_loan_application_id",
        "loan_documents",
        ["loan_application_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_loan_documents_loan_application_id", table_name="loan_documents")
    op.drop_index("ix_loan_documents_org_stage_type", table_name="loan_documents")
    op.drop_index("ix_loan_documents_org_id", table_name="loan_documents")
    op.drop_table("loan_documents")

    op.drop_index("ix_loan_workflow_stages_loan_application_id", table_name="loan_workflow_stages")
    op.drop_index("ix_loan_workflow_stages_org_stage_status", table_name="loan_workflow_stages")
    op.drop_index("ix_loan_workflow_stages_org_id", table_name="loan_workflow_stages")
    op.drop_table("loan_workflow_stages")

    op.drop_constraint("ck_loan_app_status", "loan_applications", type_="check")
    op.create_check_constraint(
        "ck_loan_app_status",
        "loan_applications",
        "status IN ('DRAFT', 'SUBMITTED', 'CANCELLED')",
    )
