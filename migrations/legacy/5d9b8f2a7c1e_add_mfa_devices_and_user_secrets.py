"""add_mfa_devices_and_user_secrets

Revision ID: 5d9b8f2a7c1e
Revises: 4b2f1c6e8a9d
Create Date: 2026-02-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5d9b8f2a7c1e"
down_revision: Union[str, None] = "4b2f1c6e8a9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_secret_encrypted", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("mfa_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "org_settings",
        sa.Column("remember_device_days", sa.Integer(), nullable=False, server_default="30"),
    )

    op.create_table(
        "user_mfa_devices",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_user_mfa_devices_token_hash"),
    )
    op.create_index(op.f("ix_user_mfa_devices_org_id"), "user_mfa_devices", ["org_id"], unique=False)
    op.create_index(op.f("ix_user_mfa_devices_user_id"), "user_mfa_devices", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_mfa_devices_token_hash"), "user_mfa_devices", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_mfa_devices_token_hash"), table_name="user_mfa_devices")
    op.drop_index(op.f("ix_user_mfa_devices_user_id"), table_name="user_mfa_devices")
    op.drop_index(op.f("ix_user_mfa_devices_org_id"), table_name="user_mfa_devices")
    op.drop_table("user_mfa_devices")

    op.drop_column("users", "mfa_confirmed_at")
    op.drop_column("users", "mfa_secret_encrypted")
    op.drop_column("org_settings", "remember_device_days")
