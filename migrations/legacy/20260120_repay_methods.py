"""restrict repayment methods to balloon and principal_and_interest

Revision ID: 20260120_repay_methods
Revises: 20260120_audit_log_enhancements
Create Date: 2026-01-20
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260120_repay_methods"
down_revision = "20260120_audit_log_enhancements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace INTEREST_ONLY with BALLOON in existing data
    op.execute(
        "UPDATE loan_applications SET repayment_method = 'BALLOON' "
        "WHERE repayment_method = 'INTEREST_ONLY'"
    )
    op.execute(
        """
        UPDATE org_settings
        SET allowed_repayment_methods = (
            SELECT jsonb_agg(DISTINCT to_jsonb(
                CASE
                    WHEN value = 'INTEREST_ONLY' THEN 'BALLOON'
                    ELSE value
                END
            ))
            FROM jsonb_array_elements_text(allowed_repayment_methods) AS value
        )
        """
    )
    op.alter_column(
        "org_settings",
        "allowed_repayment_methods",
        server_default=sa.text('\'["BALLOON", "PRINCIPAL_AND_INTEREST"]\'::jsonb'),
    )
    op.drop_constraint("ck_loan_app_repayment_method", "loan_applications", type_="check")
    op.create_check_constraint(
        "ck_loan_app_repayment_method",
        "loan_applications",
        "repayment_method IN ('BALLOON', 'PRINCIPAL_AND_INTEREST')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_loan_app_repayment_method", "loan_applications", type_="check")
    op.create_check_constraint(
        "ck_loan_app_repayment_method",
        "loan_applications",
        "repayment_method IN ('BALLOON', 'PRINCIPAL_AND_INTEREST', 'INTEREST_ONLY')",
    )
    op.alter_column(
        "org_settings",
        "allowed_repayment_methods",
        server_default=sa.text('\'["BALLOON", "PRINCIPAL_AND_INTEREST", "INTEREST_ONLY"]\'::jsonb'),
    )
