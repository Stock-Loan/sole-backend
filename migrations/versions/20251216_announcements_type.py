"""Add type to announcements"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251216_announcements_type"
down_revision = "20251216_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "announcements",
        sa.Column("type", sa.String(length=50), nullable=False, server_default="ORGANIZATION_UPDATE"),
    )
    # ensure existing rows have the default
    op.execute("UPDATE announcements SET type = 'ORGANIZATION_UPDATE' WHERE type IS NULL")
    op.alter_column("announcements", "type", server_default=None)


def downgrade() -> None:
    op.drop_column("announcements", "type")
