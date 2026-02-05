"""add user permissions table

Revision ID: 20260119_user_permissions
Revises: 0b35ba921b5c
Create Date: 2026-01-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260119_user_permissions"
down_revision = "0b35ba921b5c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("org_id", "user_id", name="uq_user_permissions_org_user"),
    )
    op.create_index("ix_user_permissions_org_id", "user_permissions", ["org_id"])
    op.create_index("ix_user_permissions_user_id", "user_permissions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_permissions_user_id", table_name="user_permissions")
    op.drop_index("ix_user_permissions_org_id", table_name="user_permissions")
    op.drop_table("user_permissions")
