"""add policy version snapshots

Revision ID: 20260110_policy_version
Revises: 20260109_loan_admin_reason
Create Date: 2026-01-10
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260110_policy_version"
down_revision = "20260109_loan_admin_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "loan_applications",
        sa.Column("policy_version_snapshot", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("loan_applications", "policy_version_snapshot")
    op.drop_column("org_settings", "policy_version")
