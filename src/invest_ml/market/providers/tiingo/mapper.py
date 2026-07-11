"""Map Tiingo API responses to provider-independent domain models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from invest_ml.market.models import (
    AssetMetadata,
    DailyBar,
    EquityInstrument,
    HistoricalBars,
    MarketCapitalizationObservation,
)
from invest_ml.market.providers.tiingo.models import (
    TiingoBarResponse,
    TiingoFundamentalsRow,
    TiingoMetadataResponse,
)

_ADJUSTMENT_METHOD = "split_and_dividend_adjusted"


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value[:10])


def _to_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def map_tiingo_metadata(
    instrument: EquityInstrument,
    response: TiingoMetadataResponse,
) -> AssetMetadata:
    return AssetMetadata(
        canonical_ticker=instrument.ticker,
        provider_ticker=response.ticker,
        provider_name=response.name,
        provider_exchange=response.exchangeCode,
        provider_start_date=_parse_date(response.startDate),
        provider_end_date=_parse_date(response.endDate),
        metadata={
            "tiingo_ticker": response.ticker,
            "tiingo_exchange": response.exchangeCode,
        },
    )


def map_tiingo_bar(raw: TiingoBarResponse) -> DailyBar:
    return DailyBar(
        trading_date=_parse_date(raw.date),  # type: ignore[arg-type]
        open=_to_decimal(raw.open),
        high=_to_decimal(raw.high),
        low=_to_decimal(raw.low),
        close=Decimal(str(raw.close)),
        volume=_to_decimal(raw.volume),
        adjusted_open=_to_decimal(raw.adjOpen),
        adjusted_high=_to_decimal(raw.adjHigh),
        adjusted_low=_to_decimal(raw.adjLow),
        adjusted_close=_to_decimal(raw.adjClose),
        adjusted_volume=_to_decimal(raw.adjVolume),
        dividend_cash=_to_decimal(raw.divCash),
        split_factor=_to_decimal(raw.splitFactor),
    )


def map_tiingo_bars(
    instrument: EquityInstrument,
    asset_metadata: AssetMetadata,
    raw_bars: list[TiingoBarResponse],
    currency: str | None,
    provider_metadata: dict[str, Any],
) -> HistoricalBars:
    bars = tuple(map_tiingo_bar(b) for b in raw_bars)
    return HistoricalBars(
        instrument=instrument,
        asset_metadata=asset_metadata,
        bars=bars,
        currency=currency,
        adjustment_method=_ADJUSTMENT_METHOD,
        provider_metadata=provider_metadata,
    )


def map_tiingo_market_cap(
    row: TiingoFundamentalsRow,
) -> MarketCapitalizationObservation | None:
    if row.marketCap is None:
        return None
    obs_date = _parse_date(row.date)
    if obs_date is None:
        return None
    return MarketCapitalizationObservation(
        observation_date=obs_date,
        market_cap=Decimal(str(row.marketCap)),
        currency="USD",
        provider_metadata={"tiingo_date": row.date},
    )
