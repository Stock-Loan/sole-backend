"""use years for service duration rules

Revision ID: 20260110_service_duration_years
Revises: 20260110_policy_version
Create Date: 2026-01-10
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260110_service_duration_years"
down_revision = "20260110_policy_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "org_settings",
        "min_service_duration_days",
        new_column_name="min_service_duration_years",
    )
    op.alter_column(
        "org_settings",
        "min_service_duration_years",
        type_=sa.Numeric(6, 2),
        postgresql_using="min_service_duration_years::numeric",
    )
    op.execute(
        """
        UPDATE org_settings
        SET min_service_duration_years = (min_service_duration_years / 365.25)
        WHERE min_service_duration_years IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE org_settings
        SET min_service_duration_years = (min_service_duration_years * 365.25)
        WHERE min_service_duration_years IS NOT NULL
        """
    )
    op.alter_column(
        "org_settings",
        "min_service_duration_years",
        type_=sa.Integer(),
        postgresql_using="min_service_duration_years::integer",
    )
    op.alter_column(
        "org_settings",
        "min_service_duration_years",
        new_column_name="min_service_duration_days",
    )
