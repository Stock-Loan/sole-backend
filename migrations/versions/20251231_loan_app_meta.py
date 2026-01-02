"""add loan application metadata and idempotency fields

Revision ID: 20251231_loan_app_meta
Revises: 20251231_loan_applications
Create Date: 2025-12-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251231_loan_app_meta"
down_revision = "20251231_loan_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loan_applications",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "loan_applications",
        sa.Column("create_idempotency_key", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "loan_applications",
        sa.Column("submit_idempotency_key", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "loan_applications",
        sa.Column("selection_value_snapshot", sa.Numeric(18, 6), nullable=False, server_default="0"),
    )
    op.add_column(
        "loan_applications",
        sa.Column(
            "quote_inputs_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "loan_applications",
        sa.Column(
            "quote_option_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "loan_applications",
        sa.Column(
            "allocation_strategy",
            sa.String(length=50),
            nullable=False,
            server_default="OLDEST_VESTED_FIRST",
        ),
    )
    op.add_column(
        "loan_applications",
        sa.Column(
            "allocation_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_loan_app_selection_value_nonneg",
        "loan_applications",
        "selection_value_snapshot >= 0",
    )
    op.create_check_constraint(
        "ck_loan_app_version_positive",
        "loan_applications",
        "version >= 1",
    )
    op.create_check_constraint(
        "ck_loan_app_allocation_strategy",
        "loan_applications",
        "allocation_strategy IN ('OLDEST_VESTED_FIRST')",
    )
    op.create_index(
        "ix_loan_applications_org_member_status_created",
        "loan_applications",
        ["org_id", "org_membership_id", "status", "created_at"],
    )
    op.create_index(
        "ux_loan_app_create_idempotency",
        "loan_applications",
        ["org_id", "org_membership_id", "create_idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ux_loan_app_submit_idempotency",
        "loan_applications",
        ["org_id", "org_membership_id", "submit_idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_loan_app_submit_idempotency", table_name="loan_applications")
    op.drop_index("ux_loan_app_create_idempotency", table_name="loan_applications")
    op.drop_index("ix_loan_applications_org_member_status_created", table_name="loan_applications")
    op.drop_constraint(
        "ck_loan_app_allocation_strategy", "loan_applications", type_="check"
    )
    op.drop_constraint(
        "ck_loan_app_version_positive", "loan_applications", type_="check"
    )
    op.drop_constraint(
        "ck_loan_app_selection_value_nonneg", "loan_applications", type_="check"
    )
    op.drop_column("loan_applications", "allocation_snapshot")
    op.drop_column("loan_applications", "allocation_strategy")
    op.drop_column("loan_applications", "quote_option_snapshot")
    op.drop_column("loan_applications", "quote_inputs_snapshot")
    op.drop_column("loan_applications", "selection_value_snapshot")
    op.drop_column("loan_applications", "submit_idempotency_key")
    op.drop_column("loan_applications", "create_idempotency_key")
    op.drop_column("loan_applications", "version")
