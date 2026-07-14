"""Drop unused market_cap column from price_bars.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-13
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("price_bars", "market_cap")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column(
        "price_bars",
        sa.Column("market_cap", sa.Numeric(), nullable=True),
    )
