"""add_pbgc_mid_term_rates

Revision ID: 2c1c7f6d0b4f
Revises: 0b35ba921b5c
Create Date: 2026-01-20 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2c1c7f6d0b4f"
down_revision: Union[str, None] = "0b35ba921b5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pbgc_mid_term_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("annual_rate_percent", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("monthly_rate_percent", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("year", "month", name="uq_pbgc_mid_term_rates_year_month"),
    )
    op.create_index(op.f("ix_pbgc_mid_term_rates_month"), "pbgc_mid_term_rates", ["month"], unique=False)
    op.create_index(op.f("ix_pbgc_mid_term_rates_year"), "pbgc_mid_term_rates", ["year"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_pbgc_mid_term_rates_year"), table_name="pbgc_mid_term_rates")
    op.drop_index(op.f("ix_pbgc_mid_term_rates_month"), table_name="pbgc_mid_term_rates")
    op.drop_table("pbgc_mid_term_rates")