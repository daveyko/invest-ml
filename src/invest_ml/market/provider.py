"""Provider protocol definitions for market data.

All concrete providers must implement one or both of these protocols.
Generic code (calculator, service, repository) must NOT import from
invest_ml.market.providers.* — only from this module and models.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from invest_ml.market.models import (
    AssetMetadata,
    EquityInstrument,
    HistoricalBars,
    MarketCapitalizationObservation,
)


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_asset_metadata: bool
    supports_daily_bars: bool
    supports_raw_prices: bool
    supports_adjusted_prices: bool
    supports_split_adjustments: bool
    supports_dividend_adjustments: bool
    supports_corporate_actions: bool
    supports_current_market_cap: bool
    supports_historical_market_cap: bool


@runtime_checkable
class EquityPriceProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def adapter_version(self) -> str: ...

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def fetch_asset_metadata(self, instrument: EquityInstrument) -> AssetMetadata: ...

    def fetch_daily_bars(
        self,
        instrument: EquityInstrument,
        *,
        start_date: date,
        end_date: date,
    ) -> HistoricalBars: ...


@runtime_checkable
class MarketCapitalizationProvider(Protocol):
    @property
    def name(self) -> str: ...

    def fetch_market_cap(
        self,
        instrument: EquityInstrument,
        *,
        as_of_date: date,
    ) -> MarketCapitalizationObservation | None: ...
