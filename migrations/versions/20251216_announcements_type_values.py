"""Normalize announcement types to new catalog"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251216_announcements_type_values"
down_revision = "20251216_announcements_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Set new default
    op.alter_column(
        "announcements",
        "type",
        existing_type=sa.String(length=50),
        server_default="GENERAL",
    )
    # Map old values to new catalog
    op.execute(
        """
        UPDATE announcements
        SET type = CASE
            WHEN type = 'FEATURE_RELEASE' THEN 'FEATURE'
            WHEN type = 'SYSTEM_NOTICE' THEN 'MAINTENANCE'
            WHEN type = 'ORGANIZATION_UPDATE' THEN 'GENERAL'
            ELSE 'GENERAL'
        END
        """
    )


def downgrade() -> None:
    # Revert to prior default and best-effort mapping
    op.execute(
        """
        UPDATE announcements
        SET type = CASE
            WHEN type = 'FEATURE' THEN 'FEATURE_RELEASE'
            ELSE 'ORGANIZATION_UPDATE'
        END
        """
    )
    op.alter_column(
        "announcements",
        "type",
        existing_type=sa.String(length=50),
        server_default="ORGANIZATION_UPDATE",
    )
