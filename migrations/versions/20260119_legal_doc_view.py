"""add loan document view to legal system role

Revision ID: 20260119_legal_doc_view
Revises: d5ab2d27cade
Create Date: 2026-01-19
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260119_legal_doc_view"
down_revision = "d5ab2d27cade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions || '["loan.document.view"]'::jsonb,
                updated_at = now()
            WHERE name = 'LEGAL'
              AND is_system_role = true
              AND NOT (permissions @> '["loan.document.view"]'::jsonb)
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET permissions = permissions - 'loan.document.view',
                updated_at = now()
            WHERE name = 'LEGAL'
              AND is_system_role = true
              AND (permissions @> '["loan.document.view"]'::jsonb)
            """
        )
    )
