"""add employee stock grants and vesting events

Revision ID: 20251230_stock_grants
Revises: 20251230_org_settings_stock
Create Date: 2025-12-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251230_stock_grants"
down_revision = "20251230_org_settings_stock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_stock_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "org_membership_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("org_memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("grant_date", sa.Date(), nullable=False),
        sa.Column("total_shares", sa.BigInteger(), nullable=False),
        sa.Column("exercise_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="ACTIVE"),
        sa.Column("vesting_strategy", sa.String(length=50), nullable=False, server_default="SCHEDULED"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint("total_shares >= 0", name="ck_stock_grants_total_shares_nonnegative"),
        sa.CheckConstraint("exercise_price >= 0", name="ck_stock_grants_exercise_price_nonnegative"),
    )
    op.create_index("ix_employee_stock_grants_org_id", "employee_stock_grants", ["org_id"])
    op.create_index(
        "ix_employee_stock_grants_org_membership_id",
        "employee_stock_grants",
        ["org_membership_id"],
    )

    op.create_table(
        "vesting_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "grant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employee_stock_grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vest_date", sa.Date(), nullable=False),
        sa.Column("shares", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("shares >= 0", name="ck_vesting_events_shares_nonnegative"),
    )
    op.create_index("ix_vesting_events_org_id", "vesting_events", ["org_id"])
    op.create_index("ix_vesting_events_grant_id", "vesting_events", ["grant_id"])
    op.create_index("ix_vesting_events_vest_date", "vesting_events", ["vest_date"])


def downgrade() -> None:
    op.drop_index("ix_vesting_events_vest_date", table_name="vesting_events")
    op.drop_index("ix_vesting_events_grant_id", table_name="vesting_events")
    op.drop_index("ix_vesting_events_org_id", table_name="vesting_events")
    op.drop_table("vesting_events")
    op.drop_index("ix_employee_stock_grants_org_membership_id", table_name="employee_stock_grants")
    op.drop_index("ix_employee_stock_grants_org_id", table_name="employee_stock_grants")
    op.drop_table("employee_stock_grants")
