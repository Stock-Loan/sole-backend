"""extend org_settings with loan policy fields

Revision ID: 20251231_org_settings_loans
Revises: 20251230_vesting_unique
Create Date: 2025-12-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251231_org_settings_loans"
down_revision = "20251230_vesting_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column(
            "allowed_repayment_methods",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(
                "'[\"INTEREST_ONLY\", \"BALLOON\", \"PRINCIPAL_AND_INTEREST\"]'::jsonb"
            ),
        ),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "min_loan_term_months",
            sa.Integer(),
            nullable=False,
            server_default="6",
        ),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "max_loan_term_months",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "allowed_interest_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"FIXED\", \"VARIABLE\"]'::jsonb"),
        ),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "fixed_interest_rate_annual_percent",
            sa.Numeric(10, 4),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "variable_base_rate_annual_percent",
            sa.Numeric(10, 4),
            nullable=True,
        ),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "variable_margin_annual_percent",
            sa.Numeric(10, 4),
            nullable=True,
        ),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "require_down_payment",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "org_settings",
        sa.Column(
            "down_payment_percent",
            sa.Numeric(5, 2),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("org_settings", "down_payment_percent")
    op.drop_column("org_settings", "require_down_payment")
    op.drop_column("org_settings", "variable_margin_annual_percent")
    op.drop_column("org_settings", "variable_base_rate_annual_percent")
    op.drop_column("org_settings", "fixed_interest_rate_annual_percent")
    op.drop_column("org_settings", "allowed_interest_types")
    op.drop_column("org_settings", "max_loan_term_months")
    op.drop_column("org_settings", "min_loan_term_months")
    op.drop_column("org_settings", "allowed_repayment_methods")
