"""Add departments and department_id to org_memberships"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251215_departments"
down_revision = "20251213_acl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "code", name="uq_departments_org_code"),
        sa.UniqueConstraint("org_id", "name", name="uq_departments_org_name"),
    )
    op.create_index("ix_departments_org_id", "departments", ["org_id"])

    op.add_column(
        "org_memberships",
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_membership_department",
        "org_memberships",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_org_memberships_department_id", "org_memberships", ["department_id"])


def downgrade() -> None:
    op.drop_index("ix_org_memberships_department_id", table_name="org_memberships")
    op.drop_constraint("fk_membership_department", "org_memberships", type_="foreignkey")
    op.drop_column("org_memberships", "department_id")
    op.drop_index("ix_departments_org_id", table_name="departments")
    op.drop_table("departments")
