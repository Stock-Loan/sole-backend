"""add loan decision reason

Revision ID: 20260109_loan_admin_reason
Revises: 20260109_loan_activation_fields
Create Date: 2026-01-09
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260109_loan_admin_reason"
down_revision = "20260109_loan_activation_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loan_applications",
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("loan_applications", "decision_reason")
