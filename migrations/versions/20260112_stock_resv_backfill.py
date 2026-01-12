"""backfill stock grant reservations for existing loan applications

Revision ID: 20260112_stock_resv_backfill
Revises: 20260110_stock_reservations
Create Date: 2026-01-12
"""

from __future__ import annotations

import json
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260112_stock_resv_backfill"
down_revision = "20260110_stock_reservations"
branch_labels = None
depends_on = None


def _iter_chunks(items: list[dict], size: int = 500):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def upgrade() -> None:
    conn = op.get_bind()

    loan_apps = sa.table(
        "loan_applications",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("org_id", sa.String()),
        sa.column("org_membership_id", postgresql.UUID(as_uuid=True)),
        sa.column("status", sa.String()),
        sa.column("allocation_snapshot", postgresql.JSONB),
    )
    reservations = sa.table(
        "stock_grant_reservations",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("org_id", sa.String()),
        sa.column("org_membership_id", postgresql.UUID(as_uuid=True)),
        sa.column("grant_id", postgresql.UUID(as_uuid=True)),
        sa.column("loan_application_id", postgresql.UUID(as_uuid=True)),
        sa.column("shares_reserved", sa.BigInteger()),
        sa.column("status", sa.String()),
    )

    stmt = sa.select(
        loan_apps.c.id,
        loan_apps.c.org_id,
        loan_apps.c.org_membership_id,
        loan_apps.c.status,
        loan_apps.c.allocation_snapshot,
    ).where(loan_apps.c.status.in_(["SUBMITTED", "IN_REVIEW", "ACTIVE"]))

    rows = []
    result = conn.execute(stmt)
    for row in result:
        allocation = row.allocation_snapshot or []
        if isinstance(allocation, str):
            try:
                allocation = json.loads(allocation)
            except json.JSONDecodeError:
                continue
        if not isinstance(allocation, list):
            continue
        for item in allocation:
            if not isinstance(item, dict):
                continue
            grant_id = item.get("grant_id")
            shares = item.get("shares")
            if not grant_id or shares is None:
                continue
            try:
                grant_uuid = uuid.UUID(str(grant_id))
            except (TypeError, ValueError):
                continue
            try:
                shares_int = int(shares)
            except (TypeError, ValueError):
                continue
            if shares_int <= 0:
                continue
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "org_id": row.org_id,
                    "org_membership_id": row.org_membership_id,
                    "grant_id": grant_uuid,
                    "loan_application_id": row.id,
                    "shares_reserved": shares_int,
                    "status": row.status,
                }
            )

    if not rows:
        return

    for chunk in _iter_chunks(rows):
        insert_stmt = postgresql.insert(reservations).values(chunk)
        insert_stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=["loan_application_id", "grant_id"]
        )
        conn.execute(insert_stmt)


def downgrade() -> None:
    # No-op: reservations are derived from loan application snapshots.
    pass
