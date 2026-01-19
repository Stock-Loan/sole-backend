"""add loan schedule view to finance system role

Revision ID: 20260119_finance_schedule_view
Revises: 20260119_legal_doc_view
Create Date: 2026-01-19
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260119_finance_schedule_view"
down_revision = "20260119_legal_doc_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions || '["loan.schedule.view"]'::jsonb,
                updated_at = now()
            WHERE name = 'FINANCE'
              AND is_system_role = true
              AND NOT (permissions @> '["loan.schedule.view"]'::jsonb)
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions - 'loan.schedule.view',
                updated_at = now()
            WHERE name = 'FINANCE'
              AND is_system_role = true
              AND (permissions @> '["loan.schedule.view"]'::jsonb)
            """
        )
    )
