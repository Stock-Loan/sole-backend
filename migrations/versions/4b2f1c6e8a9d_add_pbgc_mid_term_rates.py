"""merge pbgc mid term rates

Revision ID: 4b2f1c6e8a9d
Revises: 0b35ba921b5c
Create Date: 2026-01-20 10:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4b2f1c6e8a9d"
down_revision: Union[str, None] = "2c1c7f6d0b4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass