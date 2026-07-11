from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from invest_ml.db.base import Base


class PriceBar(Base):
    """Daily OHLCV price bar for a security.

    adjusted_close accounts for splits and dividends and is the authoritative
    price for return calculations.

    Composite PK on (security_id, trading_date, source) so multiple data
    providers can coexist; prefer adjusted_close from the highest-quality source.
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
    open: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    high: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    low: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    adjusted_close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality_flags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
