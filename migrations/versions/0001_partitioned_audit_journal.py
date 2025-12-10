"""Create partitioned audit_logs and journal_entries tables"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_partitioned_audit_journal"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=255), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", "org_id"),
        postgresql_partition_by="LIST (org_id)",
    )
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])
    op.execute("CREATE TABLE audit_logs_default PARTITION OF audit_logs FOR VALUES IN ('default')")

    op.create_table(
        "journal_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("debit_account", sa.String(length=255), nullable=False),
        sa.Column("credit_account", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tax_id", sa.LargeBinary(), nullable=True),
        sa.Column("bank_account_number", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("id", "org_id"),
        postgresql_partition_by="LIST (org_id)",
    )
    op.create_index("ix_journal_entries_org_id", "journal_entries", ["org_id"])
    op.execute("CREATE TABLE journal_entries_default PARTITION OF journal_entries FOR VALUES IN ('default')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS journal_entries_default")
    op.drop_index("ix_journal_entries_org_id", table_name="journal_entries")
    op.drop_table("journal_entries")

    op.execute("DROP TABLE IF EXISTS audit_logs_default")
    op.drop_index("ix_audit_logs_org_id", table_name="audit_logs")
    op.drop_table("audit_logs")
