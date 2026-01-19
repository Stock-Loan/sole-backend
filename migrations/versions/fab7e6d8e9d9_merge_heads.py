"""merge heads

Revision ID: fab7e6d8e9d9
Revises: 20260118_loan_repayment_evidence, 20260119_audit_part_backfill
Create Date: 2026-01-19 18:01:12.976473

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fab7e6d8e9d9'
down_revision: Union[str, None] = ('20260118_loan_repayment_evidence', '20260119_audit_part_backfill')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
