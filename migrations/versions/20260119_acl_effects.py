"""add allow/deny effects and expiry to permissions

Revision ID: 20260119_acl_effects
Revises: 20260119_user_permissions
Create Date: 2026-01-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260119_acl_effects"
down_revision = "20260119_user_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "access_control_list",
        sa.Column("effect", sa.String(length=10), nullable=False, server_default="allow"),
    )
    op.add_column(
        "access_control_list",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "access_control_list",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "user_permissions",
        sa.Column("effect", sa.String(length=10), nullable=False, server_default="allow"),
    )
    op.add_column(
        "user_permissions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_permissions", "expires_at")
    op.drop_column("user_permissions", "effect")
    op.drop_column("access_control_list", "updated_at")
    op.drop_column("access_control_list", "expires_at")
    op.drop_column("access_control_list", "effect")
