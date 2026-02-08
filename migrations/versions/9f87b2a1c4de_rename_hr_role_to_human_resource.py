"""rename HR role label to Human Resource

Revision ID: 9f87b2a1c4de
Revises: 5e2a6b9d3f1c
Create Date: 2026-02-08 00:00:01.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f87b2a1c4de"
down_revision: Union[str, None] = "5e2a6b9d3f1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename legacy system role label where the target label does not already exist.
    op.execute(
        """
        UPDATE roles AS r
        SET name = 'Human Resource'
        WHERE r.is_system_role = TRUE
          AND r.name = 'HR'
          AND NOT EXISTS (
              SELECT 1
              FROM roles AS r2
              WHERE r2.org_id = r.org_id
                AND r2.name = 'Human Resource'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles AS r
        SET name = 'HR'
        WHERE r.is_system_role = TRUE
          AND r.name = 'Human Resource'
          AND NOT EXISTS (
              SELECT 1
              FROM roles AS r2
              WHERE r2.org_id = r.org_id
                AND r2.name = 'HR'
          )
        """
    )

