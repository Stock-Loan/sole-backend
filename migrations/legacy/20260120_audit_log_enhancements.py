"""add audit log summary and changes

Revision ID: 20260120_audit_log_enhancements
Revises: 20260119_merge_acl_mfa
Create Date: 2026-01-20
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260120_audit_log_enhancements"
down_revision = "20260119_merge_acl_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("changes", sa.JSON(), nullable=True))
    op.add_column("audit_logs", sa.Column("summary", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "summary")
    op.drop_column("audit_logs", "changes")
