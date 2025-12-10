"""Create users table"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_users"
down_revision = "0001_partitioned_audit_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_active_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            server_onupdate=sa.func.now(),
        ),
        sa.UniqueConstraint("org_id", "email", name="uq_users_org_email"),
    )
    op.create_index("ix_users_org_id", "users", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_users_org_id", table_name="users")
    op.drop_table("users")
