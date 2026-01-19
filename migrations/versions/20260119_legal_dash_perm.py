"""add loan dashboard view to legal system role

Revision ID: 20260119_legal_dash_perm
Revises: 20260119_audit_part_backfill
Create Date: 2026-01-19
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260119_legal_dash_perm"
down_revision = "20260119_audit_part_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions || '["loan.dashboard.view"]'::jsonb,
                updated_at = now()
            WHERE name = 'LEGAL'
              AND is_system_role = true
              AND NOT (permissions @> '["loan.dashboard.view"]'::jsonb)
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions - 'loan.dashboard.view',
                updated_at = now()
            WHERE name = 'LEGAL'
              AND is_system_role = true
              AND (permissions @> '["loan.dashboard.view"]'::jsonb)
            """
        )
    )
