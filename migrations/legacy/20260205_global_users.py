"""global users with memberships

Revision ID: 20260205_global_users
Revises: 20260121_storage_meta
Create Date: 2026-02-05
"""

from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict

from alembic import op
import sqlalchemy as sa


revision = "20260205_global_users"
down_revision = "20260121_storage_meta"
branch_labels = None
depends_on = None


USER_FK_UPDATES = [
    ("org_memberships", "user_id"),
    ("user_roles", "user_id"),
    ("user_permissions", "user_id"),
    ("access_control_lists", "user_id"),
    ("announcement_reads", "user_id"),
    ("user_mfa_devices", "user_id"),
    ("user_mfa_recovery_codes", "user_id"),
    ("loan_repayments", "recorded_by_user_id"),
    ("loan_documents", "uploaded_by_user_id"),
    ("loan_workflow_stages", "completed_by_user_id"),
    ("loan_workflow_stages", "assigned_to_user_id"),
    ("loan_workflow_stages", "assigned_by_user_id"),
    ("org_document_templates", "uploaded_by_user_id"),
]


def _normalize_dt(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _pick_canonical(rows: list[dict]) -> dict:
    superusers = [row for row in rows if row.get("is_superuser")]
    candidates = superusers or rows
    active = [row for row in candidates if row.get("last_active_at")]
    if active:
        return max(active, key=lambda r: _normalize_dt(r.get("last_active_at")))
    return min(candidates, key=lambda r: _normalize_dt(r.get("created_at")))


def _dedupe_users_by_email(bind) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, email, is_superuser, created_at, last_active_at
            FROM users
            ORDER BY email
            """
        )
    ).mappings().all()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        email = row.get("email")
        if not email:
            continue
        grouped[email].append(row)

    for email, group in grouped.items():
        if len(group) <= 1:
            continue
        canonical = _pick_canonical(group)
        canonical_id = canonical["id"]
        for row in group:
            dup_id = row["id"]
            if dup_id == canonical_id:
                continue
            for table, column in USER_FK_UPDATES:
                bind.execute(
                    sa.text(
                        f"UPDATE {table} SET {column} = :canonical WHERE {column} = :duplicate"
                    ),
                    {"canonical": canonical_id, "duplicate": dup_id},
                )
            bind.execute(sa.text("DELETE FROM users WHERE id = :duplicate"), {"duplicate": dup_id})


def upgrade() -> None:
    bind = op.get_bind()
    _dedupe_users_by_email(bind)

    op.drop_constraint("uq_users_org_email", "users", type_="unique")
    op.drop_index("ix_users_org_id", table_name="users")
    op.drop_column("users", "org_id")
    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade() -> None:
    # Lossy downgrade: restores org_id column without reconstructing original values.
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.add_column("users", sa.Column("org_id", sa.String(), nullable=True))
    op.create_index("ix_users_org_id", "users", ["org_id"], unique=False)
    op.create_unique_constraint("uq_users_org_email", "users", ["org_id", "email"])
