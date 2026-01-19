"""merge heads

Revision ID: d5ab2d27cade
Revises: 20260119_legal_dash_perm, fab7e6d8e9d9
Create Date: 2026-01-19 19:13:08.840502

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5ab2d27cade'
down_revision: Union[str, None] = ('20260119_legal_dash_perm', 'fab7e6d8e9d9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
