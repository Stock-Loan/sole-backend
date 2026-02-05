"""add mfa required actions to org settings

Revision ID: 9f1a2c3d4e5f
Revises: 5d9b8f2a7c1e
Create Date: 2026-01-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "9f1a2c3d4e5f"
down_revision = "5d9b8f2a7c1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column(
            "mfa_required_actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("org_settings", "mfa_required_actions")
