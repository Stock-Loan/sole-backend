"""add stock grant reservations

Revision ID: 20260110_stock_reservations
Revises: 20260110_service_duration_years
Create Date: 2026-01-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260110_stock_reservations"
down_revision = "20260110_service_duration_years"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_grant_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("org_membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("loan_application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shares_reserved", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="SUBMITTED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_membership_id"], ["org_memberships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["grant_id"], ["employee_stock_grants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["loan_application_id"], ["loan_applications.id"], ondelete="CASCADE"),
        sa.CheckConstraint("shares_reserved > 0", name="ck_stock_grant_reservation_shares_positive"),
    )
    op.create_index(
        "ix_stock_grant_reservations_org_id",
        "stock_grant_reservations",
        ["org_id"],
    )
    op.create_index(
        "ix_stock_grant_reservations_org_member",
        "stock_grant_reservations",
        ["org_id", "org_membership_id"],
    )
    op.create_index(
        "ix_stock_grant_reservations_grant",
        "stock_grant_reservations",
        ["grant_id"],
    )
    op.create_index(
        "ix_stock_grant_reservations_loan",
        "stock_grant_reservations",
        ["loan_application_id"],
    )
    op.create_index(
        "ux_stock_grant_reservation_app_grant",
        "stock_grant_reservations",
        ["loan_application_id", "grant_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_stock_grant_reservation_app_grant", table_name="stock_grant_reservations")
    op.drop_index("ix_stock_grant_reservations_loan", table_name="stock_grant_reservations")
    op.drop_index("ix_stock_grant_reservations_grant", table_name="stock_grant_reservations")
    op.drop_index("ix_stock_grant_reservations_org_member", table_name="stock_grant_reservations")
    op.drop_index("ix_stock_grant_reservations_org_id", table_name="stock_grant_reservations")
    op.drop_table("stock_grant_reservations")
