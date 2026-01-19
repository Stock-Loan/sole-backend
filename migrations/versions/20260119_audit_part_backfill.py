"""backfill audit/journal partitions for existing orgs

Revision ID: 20260119_audit_part_backfill
Revises: 20260118_org_document_library
Create Date: 2026-01-19
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260119_audit_part_backfill"
down_revision = "20260118_org_document_library"
branch_labels = None
depends_on = None


def _partition_suffix(org_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", org_id).strip("_").lower()
    if not safe:
        safe = "org"
    if safe[0].isdigit():
        safe = f"org_{safe}"
    return safe


def upgrade() -> None:
    conn = op.get_bind()
    org_rows = conn.execute(sa.text("SELECT id FROM orgs")).fetchall()
    for (org_id,) in org_rows:
        suffix = _partition_suffix(org_id)
        audit_table = f"audit_logs_{suffix}"
        journal_table = f"journal_entries_{suffix}"
        org_literal = org_id.replace("'", "''")
        conn.execute(
            sa.text(
                f"CREATE TABLE IF NOT EXISTS {audit_table} "
                f"PARTITION OF audit_logs FOR VALUES IN ('{org_literal}')"
            )
        )
        conn.execute(
            sa.text(
                f"CREATE TABLE IF NOT EXISTS {journal_table} "
                f"PARTITION OF journal_entries FOR VALUES IN ('{org_literal}')"
            )
        )


def downgrade() -> None:
    # No-op: partitions may pre-exist or contain data.
    pass
