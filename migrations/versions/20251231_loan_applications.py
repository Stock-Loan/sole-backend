"""add loan applications table

Revision ID: 20251231_loan_applications
Revises: 20251231_org_settings_loans
Create Date: 2025-12-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251231_loan_applications"
down_revision = "20251231_org_settings_loans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loan_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("org_membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("selection_mode", sa.String(length=20), nullable=False),
        sa.Column("shares_to_exercise", sa.BigInteger(), nullable=False),
        sa.Column("total_exercisable_shares_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("purchase_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("down_payment_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("loan_principal", sa.Numeric(18, 6), nullable=False),
        sa.Column("interest_type", sa.String(length=20), nullable=False),
        sa.Column("repayment_method", sa.String(length=40), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("nominal_annual_rate_percent", sa.Numeric(10, 4), nullable=False),
        sa.Column("estimated_monthly_payment", sa.Numeric(18, 6), nullable=False),
        sa.Column("total_payable_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("total_interest_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("org_settings_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("eligibility_result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("marital_status_snapshot", sa.String(length=50), nullable=True),
        sa.Column("spouse_first_name", sa.String(length=100), nullable=True),
        sa.Column("spouse_middle_name", sa.String(length=100), nullable=True),
        sa.Column("spouse_last_name", sa.String(length=100), nullable=True),
        sa.Column("spouse_email", sa.String(length=255), nullable=True),
        sa.Column("spouse_phone", sa.String(length=50), nullable=True),
        sa.Column("spouse_address", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_membership_id"], ["org_memberships.id"], ondelete="CASCADE"),
        sa.CheckConstraint("shares_to_exercise >= 0", name="ck_loan_app_shares_nonneg"),
        sa.CheckConstraint(
            "total_exercisable_shares_snapshot >= 0", name="ck_loan_app_total_exercisable_nonneg"
        ),
        sa.CheckConstraint("purchase_price >= 0", name="ck_loan_app_purchase_nonneg"),
        sa.CheckConstraint("down_payment_amount >= 0", name="ck_loan_app_down_payment_nonneg"),
        sa.CheckConstraint("loan_principal >= 0", name="ck_loan_app_principal_nonneg"),
        sa.CheckConstraint("nominal_annual_rate_percent >= 0", name="ck_loan_app_rate_nonneg"),
        sa.CheckConstraint("estimated_monthly_payment >= 0", name="ck_loan_app_payment_nonneg"),
        sa.CheckConstraint("total_payable_amount >= 0", name="ck_loan_app_total_payable_nonneg"),
        sa.CheckConstraint("total_interest_amount >= 0", name="ck_loan_app_total_interest_nonneg"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'CANCELLED')", name="ck_loan_app_status"
        ),
        sa.CheckConstraint(
            "selection_mode IN ('PERCENT', 'SHARES')", name="ck_loan_app_selection_mode"
        ),
        sa.CheckConstraint(
            "interest_type IN ('FIXED', 'VARIABLE')", name="ck_loan_app_interest_type"
        ),
        sa.CheckConstraint(
            "repayment_method IN ('INTEREST_ONLY', 'BALLOON', 'PRINCIPAL_AND_INTEREST')",
            name="ck_loan_app_repayment_method",
        ),
    )
    op.create_index("ix_loan_applications_org_id", "loan_applications", ["org_id"])
    op.create_index(
        "ix_loan_applications_org_membership_id", "loan_applications", ["org_membership_id"]
    )
    op.create_index("ix_loan_applications_status", "loan_applications", ["status"])


def downgrade() -> None:
    op.drop_index("ix_loan_applications_status", table_name="loan_applications")
    op.drop_index("ix_loan_applications_org_membership_id", table_name="loan_applications")
    op.drop_index("ix_loan_applications_org_id", table_name="loan_applications")
    op.drop_table("loan_applications")
