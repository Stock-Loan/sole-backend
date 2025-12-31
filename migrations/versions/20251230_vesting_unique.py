"""add unique constraint for vesting events

Revision ID: 20251230_vesting_unique
Revises: 20251230_stock_grants
Create Date: 2025-12-30
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20251230_vesting_unique"
down_revision = "20251230_stock_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_vesting_events_grant_date",
        "vesting_events",
        ["grant_id", "vest_date"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_vesting_events_grant_date",
        "vesting_events",
        type_="unique",
    )
