"""add storage metadata fields for gcs support

Revision ID: 20260121_storage_meta
Revises: 20260120_repay_methods
Create Date: 2026-01-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260121_storage_meta"
down_revision = "20260120_repay_methods"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("assets"):
        op.add_column("assets", sa.Column("provider", sa.String(length=32), nullable=True))
        op.add_column("assets", sa.Column("checksum", sa.String(length=128), nullable=True))

    op.add_column("loan_documents", sa.Column("storage_provider", sa.String(length=32), nullable=True))
    op.add_column("loan_documents", sa.Column("storage_bucket", sa.String(length=255), nullable=True))
    op.add_column("loan_documents", sa.Column("storage_object_key", sa.String(length=1024), nullable=True))
    op.add_column("loan_documents", sa.Column("content_type", sa.String(length=100), nullable=True))
    op.add_column("loan_documents", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("loan_documents", sa.Column("checksum", sa.String(length=128), nullable=True))

    op.add_column(
        "org_document_templates",
        sa.Column("storage_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "org_document_templates",
        sa.Column("storage_bucket", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "org_document_templates",
        sa.Column("storage_object_key", sa.String(length=1024), nullable=True),
    )
    op.add_column("org_document_templates", sa.Column("content_type", sa.String(length=100), nullable=True))
    op.add_column("org_document_templates", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("org_document_templates", sa.Column("checksum", sa.String(length=128), nullable=True))

    op.add_column(
        "loan_repayments",
        sa.Column("evidence_storage_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "loan_repayments",
        sa.Column("evidence_storage_bucket", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "loan_repayments",
        sa.Column("evidence_storage_object_key", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "loan_repayments",
        sa.Column("evidence_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "loan_repayments",
        sa.Column("evidence_checksum", sa.String(length=128), nullable=True),
    )

    # Backfill object keys for existing local storage records
    op.execute(
        ""
        "UPDATE loan_documents SET storage_object_key = storage_path_or_url, storage_provider = 'local' "
        "WHERE storage_path_or_url IS NOT NULL AND storage_path_or_url NOT LIKE 'http%'"
    )
    op.execute(
        ""
        "UPDATE org_document_templates SET storage_object_key = storage_path_or_url, storage_provider = 'local' "
        "WHERE storage_path_or_url IS NOT NULL AND storage_path_or_url NOT LIKE 'http%'"
    )
    op.execute(
        ""
        "UPDATE loan_repayments SET evidence_storage_object_key = evidence_storage_path_or_url, "
        "evidence_storage_provider = 'local' "
        "WHERE evidence_storage_path_or_url IS NOT NULL AND evidence_storage_path_or_url NOT LIKE 'http%'"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    op.drop_column("loan_repayments", "evidence_checksum")
    op.drop_column("loan_repayments", "evidence_size_bytes")
    op.drop_column("loan_repayments", "evidence_storage_object_key")
    op.drop_column("loan_repayments", "evidence_storage_bucket")
    op.drop_column("loan_repayments", "evidence_storage_provider")

    op.drop_column("org_document_templates", "checksum")
    op.drop_column("org_document_templates", "size_bytes")
    op.drop_column("org_document_templates", "content_type")
    op.drop_column("org_document_templates", "storage_object_key")
    op.drop_column("org_document_templates", "storage_bucket")
    op.drop_column("org_document_templates", "storage_provider")

    op.drop_column("loan_documents", "checksum")
    op.drop_column("loan_documents", "size_bytes")
    op.drop_column("loan_documents", "content_type")
    op.drop_column("loan_documents", "storage_object_key")
    op.drop_column("loan_documents", "storage_bucket")
    op.drop_column("loan_documents", "storage_provider")

    if inspector.has_table("assets"):
        op.drop_column("assets", "checksum")
        op.drop_column("assets", "provider")
