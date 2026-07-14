"""Add price_bars columns and price_bar_sync_state table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-12

Adds:
- adjusted_open/high/low/volume, dividend_cash, split_factor, source_ticker,
  last_refreshed_at to price_bars
- price_bar_sync_state table for per-security per-source synchronization state
- Supporting indexes
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── price_bars: add missing columns ────────────────────────────────────
    op.add_column("price_bars", sa.Column("adjusted_open", sa.Numeric(), nullable=True))
    op.add_column("price_bars", sa.Column("adjusted_high", sa.Numeric(), nullable=True))
    op.add_column("price_bars", sa.Column("adjusted_low", sa.Numeric(), nullable=True))
    op.add_column("price_bars", sa.Column("adjusted_volume", sa.BigInteger(), nullable=True))
    op.add_column("price_bars", sa.Column("dividend_cash", sa.Numeric(), nullable=True))
    op.add_column("price_bars", sa.Column("split_factor", sa.Numeric(), nullable=True))
    op.add_column("price_bars", sa.Column("source_ticker", sa.Text(), nullable=True))
    op.add_column(
        "price_bars",
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── price_bars: additional query indexes ───────────────────────────────
    op.create_index(
        "ix_price_bars_security_id_trading_date",
        "price_bars",
        ["security_id", "trading_date"],
    )
    op.create_index("ix_price_bars_trading_date", "price_bars", ["trading_date"])

    # ── price_bar_sync_state ───────────────────────────────────────────────
    op.create_table(
        "price_bar_sync_state",
        sa.Column(
            "security_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("backfill_start_date", sa.Date(), nullable=False),
        sa.Column("latest_stored_trading_date", sa.Date(), nullable=True),
        sa.Column("checked_through_date", sa.Date(), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_full_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_corporate_action_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="never_synced"),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.security_id"],
            name="fk_price_bar_sync_state_security_id",
        ),
        sa.CheckConstraint(
            "status IN ('never_synced', 'running', 'succeeded', 'failed', 'unsupported')",
            name="ck_price_bar_sync_state_status",
        ),
        sa.PrimaryKeyConstraint("security_id", "source"),
    )
    op.create_index(
        "ix_price_bar_sync_state_source_status",
        "price_bar_sync_state",
        ["source", "status", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_price_bar_sync_state_source_status", table_name="price_bar_sync_state")
    op.drop_table("price_bar_sync_state")
    op.drop_index("ix_price_bars_trading_date", table_name="price_bars")
    op.drop_index("ix_price_bars_security_id_trading_date", table_name="price_bars")
    op.drop_column("price_bars", "last_refreshed_at")
    op.drop_column("price_bars", "source_ticker")
    op.drop_column("price_bars", "split_factor")
    op.drop_column("price_bars", "dividend_cash")
    op.drop_column("price_bars", "adjusted_volume")
    op.drop_column("price_bars", "adjusted_low")
    op.drop_column("price_bars", "adjusted_high")
    op.drop_column("price_bars", "adjusted_open")
