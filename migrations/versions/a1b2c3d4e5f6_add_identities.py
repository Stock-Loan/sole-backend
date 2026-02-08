"""add identities table and migrate auth columns from users

Revision ID: a1b2c3d4e5f6
Revises: 9f87b2a1c4de
Create Date: 2026-02-08 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "9f87b2a1c4de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create identities table
    op.create_table(
        "identities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mfa_method", sa.String(50), nullable=True),
        sa.Column("mfa_secret_encrypted", sa.String(255), nullable=True),
        sa.Column("mfa_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_identities_email", "identities", ["email"], unique=True)

    # 2. Populate identities from existing users.
    #    For each distinct email, take credentials from the most recently active user.
    op.execute(
        """
        INSERT INTO identities (
            id, email, hashed_password, mfa_enabled, mfa_method,
            mfa_secret_encrypted, mfa_confirmed_at, token_version,
            last_active_at, must_change_password, is_active,
            created_at, updated_at
        )
        SELECT DISTINCT ON (lower(email))
            gen_random_uuid(),
            email,
            hashed_password,
            mfa_enabled,
            mfa_method,
            mfa_secret_encrypted,
            mfa_confirmed_at,
            token_version,
            last_active_at,
            must_change_password,
            is_active,
            created_at,
            now()
        FROM users
        ORDER BY lower(email), last_active_at DESC NULLS LAST
        """
    )

    # 3. Add identity_id column to users (nullable initially for backfill)
    op.add_column(
        "users",
        sa.Column("identity_id", UUID(as_uuid=True), nullable=True),
    )

    # 4. Backfill identity_id on users by matching email
    op.execute(
        """
        UPDATE users u
        SET identity_id = i.id
        FROM identities i
        WHERE lower(u.email) = lower(i.email)
        """
    )

    # 5. Make identity_id NOT NULL
    op.alter_column("users", "identity_id", nullable=False)

    # 6. Add FK constraint and index
    op.create_foreign_key(
        "fk_users_identity_id",
        "users",
        "identities",
        ["identity_id"],
        ["id"],
    )
    op.create_index("ix_users_identity_id", "users", ["identity_id"])

    # 7. Add unique constraint (org_id, identity_id)
    op.create_unique_constraint("uq_users_org_identity", "users", ["org_id", "identity_id"])

    # 8. Drop auth columns from users (moved to identities)
    op.drop_column("users", "hashed_password")
    op.drop_column("users", "mfa_enabled")
    op.drop_column("users", "mfa_method")
    op.drop_column("users", "mfa_secret_encrypted")
    op.drop_column("users", "mfa_confirmed_at")
    op.drop_column("users", "token_version")
    op.drop_column("users", "last_active_at")
    op.drop_column("users", "must_change_password")

    # 9. Migrate user_mfa_recovery_codes: add identity_id, backfill, drop old columns
    op.add_column(
        "user_mfa_recovery_codes",
        sa.Column("identity_id", UUID(as_uuid=True), nullable=True),
    )

    # Backfill identity_id from users (user_id → users.identity_id)
    op.execute(
        """
        UPDATE user_mfa_recovery_codes rc
        SET identity_id = u.identity_id
        FROM users u
        WHERE rc.user_id = u.id
        """
    )

    # Delete orphan recovery codes that couldn't be linked
    op.execute(
        """
        DELETE FROM user_mfa_recovery_codes
        WHERE identity_id IS NULL
        """
    )

    # Deduplicate: when multiple users shared an email (and now identity),
    # keep only recovery codes from one set. For each identity, keep codes
    # from the earliest created_at batch (they are all equivalent).
    op.execute(
        """
        DELETE FROM user_mfa_recovery_codes
        WHERE id IN (
            SELECT rc.id
            FROM user_mfa_recovery_codes rc
            JOIN (
                SELECT identity_id, MIN(created_at) AS min_created
                FROM user_mfa_recovery_codes
                WHERE used_at IS NULL
                GROUP BY identity_id
            ) keep ON rc.identity_id = keep.identity_id
            WHERE rc.created_at > keep.min_created
              AND rc.used_at IS NULL
        )
        """
    )

    op.alter_column("user_mfa_recovery_codes", "identity_id", nullable=False)

    # Drop old FK columns
    op.drop_column("user_mfa_recovery_codes", "org_id")
    op.drop_column("user_mfa_recovery_codes", "user_id")

    # Add FK and index for identity_id
    op.create_foreign_key(
        "fk_recovery_identity",
        "user_mfa_recovery_codes",
        "identities",
        ["identity_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_user_mfa_recovery_codes_identity_id",
        "user_mfa_recovery_codes",
        ["identity_id"],
    )


def downgrade() -> None:
    # Reverse recovery codes migration
    op.drop_index("ix_user_mfa_recovery_codes_identity_id", table_name="user_mfa_recovery_codes")
    op.drop_constraint("fk_recovery_identity", "user_mfa_recovery_codes", type_="foreignkey")

    op.add_column(
        "user_mfa_recovery_codes",
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "user_mfa_recovery_codes",
        sa.Column("org_id", sa.String(), nullable=True),
    )

    # Best-effort backfill: pick the first user per identity
    op.execute(
        """
        UPDATE user_mfa_recovery_codes rc
        SET user_id = u.id, org_id = u.org_id
        FROM (
            SELECT DISTINCT ON (identity_id) id, org_id, identity_id
            FROM users
            ORDER BY identity_id, created_at ASC
        ) u
        WHERE rc.identity_id = u.identity_id
        """
    )

    # Delete any that couldn't be linked
    op.execute("DELETE FROM user_mfa_recovery_codes WHERE user_id IS NULL")

    op.alter_column("user_mfa_recovery_codes", "user_id", nullable=False)
    op.alter_column("user_mfa_recovery_codes", "org_id", nullable=False)

    op.create_foreign_key(None, "user_mfa_recovery_codes", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(None, "user_mfa_recovery_codes", "orgs", ["org_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_user_mfa_recovery_codes_user_id", "user_mfa_recovery_codes", ["user_id"])
    op.create_index("ix_user_mfa_recovery_codes_org_id", "user_mfa_recovery_codes", ["org_id"])

    op.drop_column("user_mfa_recovery_codes", "identity_id")

    # Reverse users migration: re-add auth columns
    op.add_column("users", sa.Column("hashed_password", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), server_default="false", nullable=True))
    op.add_column("users", sa.Column("mfa_method", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("mfa_secret_encrypted", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("mfa_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("token_version", sa.Integer(), server_default="0", nullable=True))
    op.add_column("users", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=True, server_default="false"))

    # Copy data back from identities
    op.execute(
        """
        UPDATE users u
        SET
            hashed_password = i.hashed_password,
            mfa_enabled = i.mfa_enabled,
            mfa_method = i.mfa_method,
            mfa_secret_encrypted = i.mfa_secret_encrypted,
            mfa_confirmed_at = i.mfa_confirmed_at,
            token_version = i.token_version,
            last_active_at = i.last_active_at,
            must_change_password = i.must_change_password
        FROM identities i
        WHERE u.identity_id = i.id
        """
    )

    op.alter_column("users", "hashed_password", nullable=False)
    op.alter_column("users", "mfa_enabled", nullable=False)
    op.alter_column("users", "token_version", nullable=False)
    op.alter_column("users", "must_change_password", nullable=False)

    # Drop identity_id from users
    op.drop_constraint("uq_users_org_identity", "users", type_="unique")
    op.drop_index("ix_users_identity_id", table_name="users")
    op.drop_constraint("fk_users_identity_id", "users", type_="foreignkey")
    op.drop_column("users", "identity_id")

    # Drop identities table
    op.drop_index("ix_identities_email", table_name="identities")
    op.drop_table("identities")
