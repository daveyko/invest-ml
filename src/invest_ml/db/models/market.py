from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from invest_ml.db.base import Base


class PriceBar(Base):
    """Daily OHLCV price bar for a security.

    adjusted_close accounts for splits and dividends and is the authoritative
    price for return calculations. Raw fields are preserved for auditing and
    corporate-action validation.

    Composite PK on (security_id, trading_date, source) so multiple data
    providers can coexist.
    """

    __tablename__ = "price_bars"

    security_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("securities.security_id", name="fk_price_bars_security_id"),
        primary_key=True,
        nullable=False,
    )
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    source: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)

    # Raw OHLCV
    open: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    high: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    low: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # Adjusted OHLCV (split- and dividend-adjusted)
    adjusted_open: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    adjusted_high: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    adjusted_low: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    adjusted_close: Mapped[float] = mapped_column(Numeric, nullable=False)
    adjusted_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Corporate action fields
    dividend_cash: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    split_factor: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    source_ticker: Mapped[str | None] = mapped_column(Text, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    # ingested_at = first_ingested_at; never updated after initial insert
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quality_flags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("ix_price_bars_security_id_trading_date", "security_id", "trading_date"),
        Index("ix_price_bars_trading_date", "trading_date"),
    )


class PriceBarSyncState(Base):
    """Per-security per-source synchronization state for price bar ingestion.

    checked_through_date is the key skip field: it records that this security
    was successfully queried through this provider date, even if the provider
    returned no bar on that exact date (halted, delisted, etc.).
    """

    __tablename__ = "price_bar_sync_state"

    security_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("securities.security_id", name="fk_price_bar_sync_state_security_id"),
        primary_key=True,
        nullable=False,
    )
    source: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)

    backfill_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    latest_stored_trading_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    checked_through_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_full_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reconciled_corporate_action_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="never_synced")
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('never_synced', 'running', 'succeeded', 'failed', 'unsupported')",
            name="ck_price_bar_sync_state_status",
        ),
        Index(
            "ix_price_bar_sync_state_source_status",
            "source",
            "status",
            "next_retry_at",
        ),
    )
