"""Add access_control_list table"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251213_acl"
down_revision = "20251213_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_control_list",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "user_id", "resource_type", "resource_id", name="uq_acl_org_user_resource"),
    )
    op.create_index("ix_acl_org_id", "access_control_list", ["org_id"])
    op.create_index("ix_acl_user", "access_control_list", ["user_id"])
    op.create_index("ix_acl_resource", "access_control_list", ["resource_type", "resource_id"])


def downgrade() -> None:
    op.drop_index("ix_acl_resource", table_name="access_control_list")
    op.drop_index("ix_acl_user", table_name="access_control_list")
    op.drop_index("ix_acl_org_id", table_name="access_control_list")
    op.drop_table("access_control_list")
