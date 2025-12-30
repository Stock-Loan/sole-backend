"""add stock exercise rule fields to org_settings

Revision ID: 20251230_org_settings_stock_rules
Revises: 20251217_org_settings
Create Date: 2025-12-30
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251230_org_settings_stock_rules"
down_revision = "20251217_org_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column("enforce_service_duration_rule", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("org_settings", sa.Column("min_service_duration_days", sa.Integer(), nullable=True))
    op.add_column(
        "org_settings",
        sa.Column("enforce_min_vested_to_exercise", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "org_settings",
        sa.Column("min_vested_shares_to_exercise", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("org_settings", "min_vested_shares_to_exercise")
    op.drop_column("org_settings", "enforce_min_vested_to_exercise")
    op.drop_column("org_settings", "min_service_duration_days")
    op.drop_column("org_settings", "enforce_service_duration_rule")
