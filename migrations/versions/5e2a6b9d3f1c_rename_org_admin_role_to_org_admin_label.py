"""rename ORG_ADMIN role label

Revision ID: 5e2a6b9d3f1c
Revises: 382127535a2f
Create Date: 2026-02-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e2a6b9d3f1c"
down_revision: Union[str, None] = "382127535a2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename legacy system role label where the target label does not already exist.
    op.execute(
        """
        UPDATE roles AS r
        SET name = 'ORG ADMIN'
        WHERE r.is_system_role = TRUE
          AND r.name = 'ORG_ADMIN'
          AND NOT EXISTS (
              SELECT 1
              FROM roles AS r2
              WHERE r2.org_id = r.org_id
                AND r2.name = 'ORG ADMIN'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles AS r
        SET name = 'ORG_ADMIN'
        WHERE r.is_system_role = TRUE
          AND r.name = 'ORG ADMIN'
          AND NOT EXISTS (
              SELECT 1
              FROM roles AS r2
              WHERE r2.org_id = r.org_id
                AND r2.name = 'ORG_ADMIN'
          )
        """
    )

