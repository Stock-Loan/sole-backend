"""add loan repayments table and completed status

Revision ID: 20260117_loan_repayments
Revises: 20260117_workflow_assign
Create Date: 2026-01-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260117_loan_repayments"
down_revision = "20260117_workflow_assign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loan_repayments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("loan_application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("principal_amount", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("interest_amount", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_loan_repayment_amount_nonneg"),
        sa.CheckConstraint("principal_amount >= 0", name="ck_loan_repayment_principal_nonneg"),
        sa.CheckConstraint("interest_amount >= 0", name="ck_loan_repayment_interest_nonneg"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["loan_application_id"], ["loan_applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_loan_repayments_org_id", "loan_repayments", ["org_id"])
    op.create_index(
        "ix_loan_repayments_org_loan", "loan_repayments", ["org_id", "loan_application_id"]
    )

    op.drop_constraint("ck_loan_app_status", "loan_applications", type_="check")
    op.create_check_constraint(
        "ck_loan_app_status",
        "loan_applications",
        "status IN ('DRAFT', 'SUBMITTED', 'CANCELLED', 'IN_REVIEW', 'ACTIVE', 'COMPLETED', 'REJECTED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_loan_app_status", "loan_applications", type_="check")
    op.create_check_constraint(
        "ck_loan_app_status",
        "loan_applications",
        "status IN ('DRAFT', 'SUBMITTED', 'CANCELLED', 'IN_REVIEW', 'ACTIVE', 'REJECTED')",
    )

    op.drop_index("ix_loan_repayments_org_loan", table_name="loan_repayments")
    op.drop_index("ix_loan_repayments_org_id", table_name="loan_repayments")
    op.drop_table("loan_repayments")
