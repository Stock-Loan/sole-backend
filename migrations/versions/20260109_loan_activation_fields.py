"""add loan activation fields

Revision ID: 20260109_loan_activation_fields
Revises: 20260109_loan_workflow
Create Date: 2026-01-09
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260109_loan_activation_fields"
down_revision = "20260109_loan_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loan_applications",
        sa.Column("activation_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "loan_applications",
        sa.Column("election_83b_due_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("loan_applications", "election_83b_due_date")
    op.drop_column("loan_applications", "activation_date")
