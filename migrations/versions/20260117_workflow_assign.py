"""add assignment fields to loan workflow stages

Revision ID: 20260117_workflow_assign
Revises: 20260112_stock_resv_backfill
Create Date: 2026-01-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260117_workflow_assign"
down_revision = "20260112_stock_resv_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loan_workflow_stages",
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "loan_workflow_stages",
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "loan_workflow_stages",
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_loan_workflow_stages_org_assignee",
        "loan_workflow_stages",
        ["org_id", "assigned_to_user_id"],
    )
    op.create_foreign_key(
        "fk_loan_workflow_stages_assigned_to_user",
        "loan_workflow_stages",
        "users",
        ["assigned_to_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_loan_workflow_stages_assigned_by_user",
        "loan_workflow_stages",
        "users",
        ["assigned_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_loan_workflow_stages_assigned_by_user",
        "loan_workflow_stages",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_loan_workflow_stages_assigned_to_user",
        "loan_workflow_stages",
        type_="foreignkey",
    )
    op.drop_index("ix_loan_workflow_stages_org_assignee", table_name="loan_workflow_stages")
    op.drop_column("loan_workflow_stages", "assigned_at")
    op.drop_column("loan_workflow_stages", "assigned_by_user_id")
    op.drop_column("loan_workflow_stages", "assigned_to_user_id")
