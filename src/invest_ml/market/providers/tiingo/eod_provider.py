"""TiingoEodProvider — implements EquityPriceProvider via Tiingo daily endpoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from invest_ml.market.errors import MarketDataInstrumentNotFoundError
from invest_ml.market.models import AssetMetadata, EquityInstrument, HistoricalBars
from invest_ml.market.provider import ProviderCapabilities
from invest_ml.market.providers.tiingo.client import TiingoHttpClient
from invest_ml.market.providers.tiingo.mapper import map_tiingo_bars, map_tiingo_metadata
from invest_ml.market.providers.tiingo.models import TiingoBarResponse, TiingoMetadataResponse
from invest_ml.market.providers.tiingo.symbols import SymbolResolver

logger = logging.getLogger(__name__)


@dataclass
class TiingoEodSettings:
    api_token: str
    base_url: str = "https://api.tiingo.com"
    fundamentals_enabled: bool = False
    max_retries: int = 3
    timeout: float = 30.0


class TiingoEodProvider:
    """Equity price provider backed by the Tiingo /tiingo/daily endpoints."""

    name = "tiingo"
    adapter_version = "tiingo_eod_v1"

    def __init__(
        self,
        settings: TiingoEodSettings,
        symbol_overrides: dict[str, str] | None = None,
        http_client: TiingoHttpClient | None = None,
    ) -> None:
        self._settings = settings
        self._resolver = SymbolResolver(symbol_overrides)
        self._client = http_client or TiingoHttpClient(
            api_token=settings.api_token,
            base_url=settings.base_url,
            max_retries=settings.max_retries,
            timeout=settings.timeout,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_asset_metadata=True,
            supports_daily_bars=True,
            supports_raw_prices=True,
            supports_adjusted_prices=True,
            supports_split_adjustments=True,
            supports_dividend_adjustments=True,
            supports_corporate_actions=False,
            supports_current_market_cap=self._settings.fundamentals_enabled,
            supports_historical_market_cap=self._settings.fundamentals_enabled,
        )

    def fetch_asset_metadata(self, instrument: EquityInstrument) -> AssetMetadata:
        ticker = self._resolver.resolve(instrument)
        path = f"/tiingo/daily/{ticker}"
        data: dict[str, Any] = self._client.get(path)
        response = TiingoMetadataResponse.model_validate(data)
        return map_tiingo_metadata(instrument, response)

    def fetch_daily_bars(
        self,
        instrument: EquityInstrument,
        *,
        start_date: date,
        end_date: date,
    ) -> HistoricalBars:
        ticker = self._resolver.resolve(instrument)
        path = f"/tiingo/daily/{ticker}/prices"
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "format": "json",
            "resampleFreq": "daily",
        }
        raw_data: list[dict[str, Any]] = self._client.get(path, params=params)

        if not isinstance(raw_data, list):
            raise MarketDataInstrumentNotFoundError(
                f"Tiingo returned non-list for {ticker} bars"
            )

        raw_bars = [TiingoBarResponse.model_validate(r) for r in raw_data]

        metadata = self.fetch_asset_metadata(instrument)
        return map_tiingo_bars(
            instrument=instrument,
            asset_metadata=metadata,
            raw_bars=raw_bars,
            currency="USD",
            provider_metadata={
                "tiingo_ticker": ticker,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
