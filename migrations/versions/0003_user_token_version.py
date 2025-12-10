"""Add token_version to users"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_user_token_version"
down_revision = "0002_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.alter_column("users", "token_version", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "token_version")
