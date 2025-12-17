"""org settings table

Revision ID: 20251217_org_settings
Revises: 20251216_ann_types_values
Create Date: 2025-12-17
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251217_org_settings"
down_revision = "20251216_ann_types_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_settings",
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("allow_user_data_export", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("allow_profile_edit", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("require_two_factor", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("audit_log_retention_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("inactive_user_retention_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("org_settings")
