"""Provider-agnostic domain models for market data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class EquityInstrument:
    security_id: UUID
    company_id: UUID
    ticker: str
    exchange: str | None


@dataclass(frozen=True)
class AssetMetadata:
    canonical_ticker: str
    provider_ticker: str
    provider_name: str | None
    provider_exchange: str | None
    provider_start_date: date | None
    provider_end_date: date | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class DailyBar:
    trading_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: Decimal | None
    adjusted_open: Decimal | None
    adjusted_high: Decimal | None
    adjusted_low: Decimal | None
    adjusted_close: Decimal | None
    adjusted_volume: Decimal | None
    dividend_cash: Decimal | None
    split_factor: Decimal | None


@dataclass(frozen=True)
class HistoricalBars:
    instrument: EquityInstrument
    asset_metadata: AssetMetadata
    bars: tuple[DailyBar, ...]
    currency: str | None
    adjustment_method: str
    provider_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class MarketCapitalizationObservation:
    observation_date: date
    market_cap: Decimal
    currency: str | None
    provider_metadata: Mapping[str, Any]
