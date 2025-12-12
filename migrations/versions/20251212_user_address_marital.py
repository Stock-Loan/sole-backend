"""Add marital status and address fields to users"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251212_user_address_marital"
down_revision = "20251211_orgs_memberships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("marital_status", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("country", sa.String(length=2), nullable=True))
    op.add_column("users", sa.Column("state", sa.String(length=10), nullable=True))
    op.add_column("users", sa.Column("address_line1", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("address_line2", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("postal_code", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "postal_code")
    op.drop_column("users", "address_line2")
    op.drop_column("users", "address_line1")
    op.drop_column("users", "state")
    op.drop_column("users", "country")
    op.drop_column("users", "marital_status")
