"""merge acl effects and mfa heads

Revision ID: 20260119_merge_acl_mfa
Revises: 20260119_acl_effects, a1b2c3d4e5f6
Create Date: 2026-01-19
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "20260119_merge_acl_mfa"
down_revision: Union[str, Sequence[str], None] = ("20260119_acl_effects", "a1b2c3d4e5f6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
