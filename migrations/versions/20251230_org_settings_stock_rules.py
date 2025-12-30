"""add stock exercise rule fields to org_settings

Revision ID: 20251230_org_settings_stock
Revises: 20251217_org_settings
Create Date: 2025-12-30
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251230_org_settings_stock"
down_revision = "20251217_org_settings"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    if not _column_exists("org_settings", "enforce_service_duration_rule"):
        op.add_column(
            "org_settings",
            sa.Column("enforce_service_duration_rule", sa.Boolean(), nullable=False, server_default="false"),
        )
    if not _column_exists("org_settings", "min_service_duration_days"):
        op.add_column("org_settings", sa.Column("min_service_duration_days", sa.Integer(), nullable=True))
    if not _column_exists("org_settings", "enforce_min_vested_to_exercise"):
        op.add_column(
            "org_settings",
            sa.Column("enforce_min_vested_to_exercise", sa.Boolean(), nullable=False, server_default="false"),
        )
    if not _column_exists("org_settings", "min_vested_shares_to_exercise"):
        op.add_column(
            "org_settings",
            sa.Column("min_vested_shares_to_exercise", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("org_settings", "min_vested_shares_to_exercise"):
        op.drop_column("org_settings", "min_vested_shares_to_exercise")
    if _column_exists("org_settings", "enforce_min_vested_to_exercise"):
        op.drop_column("org_settings", "enforce_min_vested_to_exercise")
    if _column_exists("org_settings", "min_service_duration_days"):
        op.drop_column("org_settings", "min_service_duration_days")
    if _column_exists("org_settings", "enforce_service_duration_rule"):
        op.drop_column("org_settings", "enforce_service_duration_rule")
