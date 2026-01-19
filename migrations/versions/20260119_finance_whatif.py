"""add loan what-if simulate to finance system role

Revision ID: 20260119_finance_whatif
Revises: 20260119_finance_whatif2
Create Date: 2026-01-19
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260119_finance_whatif"
down_revision = "20260119_finance_whatif2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions || '["loan.what_if.simulate"]'::jsonb,
                updated_at = now()
            WHERE name = 'FINANCE'
              AND is_system_role = true
              AND NOT (permissions @> '["loan.what_if.simulate"]'::jsonb)
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions - 'loan.what_if.simulate',
                updated_at = now()
            WHERE name = 'FINANCE'
              AND is_system_role = true
              AND (permissions @> '["loan.what_if.simulate"]'::jsonb)
            """
        )
    )