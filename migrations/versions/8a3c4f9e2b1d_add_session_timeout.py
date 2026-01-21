"""add session_timeout_minutes to org_settings

Revision ID: 8a3c4f9e2b1d
Revises: 5d9b8f2a7c1e
Create Date: 2026-01-21 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8a3c4f9e2b1d"
down_revision = "9f1a2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column(
            "session_timeout_minutes",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )


def downgrade() -> None:
    op.drop_column("org_settings", "session_timeout_minutes")
