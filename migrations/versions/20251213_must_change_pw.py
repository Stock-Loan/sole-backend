"""Add must_change_password flag to users"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision = "20251213_must_change_pw"
down_revision = "20251212_user_address_marital"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("users")}

    if "must_change_password" not in columns:
        op.add_column(
            "users",
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )
        # Existing users should not be forced to change passwords.
        op.execute(text("UPDATE users SET must_change_password = false"))
        # Remove the default so callers set an explicit value going forward.
        op.alter_column("users", "must_change_password", server_default=None)
    else:
        op.execute(
            text(
                "UPDATE users SET must_change_password = false WHERE must_change_password IS NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("users")}
    if "must_change_password" in columns:
        op.drop_column("users", "must_change_password")
