"""add org document library tables

Revision ID: 20260118_org_document_library
Revises: 20260117_loan_repayments
Create Date: 2026-01-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260118_org_document_library"
down_revision = "20260117_loan_repayments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_document_folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("system_key", sa.String(length=50), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "name", name="uq_org_document_folders_org_name"),
        sa.UniqueConstraint("org_id", "system_key", name="uq_org_document_folders_org_key"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_org_document_folders_org_id", "org_document_folders", ["org_id"])

    op.create_table(
        "org_document_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path_or_url", sa.String(length=1024), nullable=False),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["folder_id"], ["org_document_folders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_org_document_templates_org_id", "org_document_templates", ["org_id"])
    op.create_index(
        "ix_org_document_templates_folder_id",
        "org_document_templates",
        ["folder_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_org_document_templates_folder_id", table_name="org_document_templates")
    op.drop_index("ix_org_document_templates_org_id", table_name="org_document_templates")
    op.drop_table("org_document_templates")
    op.drop_index("ix_org_document_folders_org_id", table_name="org_document_folders")
    op.drop_table("org_document_folders")
