"""add repayment evidence fields

Revision ID: 20260118_loan_repayment_evidence
Revises: 20260118_org_document_library
Create Date: 2026-01-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260118_loan_repayment_evidence"
down_revision = "20260118_org_document_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loan_repayments", sa.Column("evidence_file_name", sa.String(length=255), nullable=True))
    op.add_column(
        "loan_repayments", sa.Column("evidence_storage_path_or_url", sa.String(length=1024), nullable=True)
    )
    op.add_column("loan_repayments", sa.Column("evidence_content_type", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("loan_repayments", "evidence_content_type")
    op.drop_column("loan_repayments", "evidence_storage_path_or_url")
    op.drop_column("loan_repayments", "evidence_file_name")
