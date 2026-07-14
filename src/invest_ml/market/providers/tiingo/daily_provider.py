"""TiingoDailyPriceProvider — implements DailyPriceProvider for price-bar ingestion."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

from invest_ml.market.errors import (
    MarketDataInvalidResponseError,
)
from invest_ml.market.models import DailyBar
from invest_ml.market.providers.tiingo.client import TiingoHttpClient
from invest_ml.market.providers.tiingo.mapper import map_tiingo_bar
from invest_ml.market.providers.tiingo.models import TiingoBarResponse, TiingoMetadataResponse
from invest_ml.market.providers.tiingo.symbols import SymbolResolver

logger = logging.getLogger(__name__)


class TiingoDailyPriceProvider:
    """Fetches EOD price bars and the latest available provider date from Tiingo.

    Uses one reference-ticker metadata call to determine the latest trading date,
    then makes per-ticker /tiingo/daily/{ticker}/prices requests for the date range.

    No per-ticker metadata requests occur during bar ingestion — the provider
    handles missing or invalid tickers as per-security failures.
    """

    def __init__(
        self,
        http_client: TiingoHttpClient,
        symbol_overrides: dict[str, str] | None = None,
    ) -> None:
        self._client = http_client
        self._resolver = SymbolResolver(symbol_overrides)

    def get_latest_available_date(self, *, reference_ticker: str) -> date:
        """Return the latest trading date available from Tiingo for a reference ticker.

        Uses the metadata endpoint — one lightweight request per materialization.
        """
        resolved = self._resolver.resolve_ticker(reference_ticker)
        path = f"/tiingo/daily/{resolved}"
        data: dict[str, Any] = self._client.get(path)
        response = TiingoMetadataResponse.model_validate(data)
        if response.endDate is None:
            raise MarketDataInvalidResponseError(
                f"Tiingo metadata for {reference_ticker!r} missing endDate"
            )
        return date.fromisoformat(response.endDate[:10])

    def get_daily_bars(
        self,
        *,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyBar]:
        """Fetch daily OHLCV bars for one ticker over the given date range.

        Raises MarketDataInstrumentNotFoundError for unknown tickers (404).
        Raises MarketDataInvalidResponseError for malformed responses.
        Other MarketDataError subtypes propagate from the HTTP client.
        """
        resolved = self._resolver.resolve_ticker(ticker)
        path = f"/tiingo/daily/{resolved}/prices"
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "format": "json",
            "resampleFreq": "daily",
        }
        raw_data: Any = self._client.get(path, params=params)
        if not isinstance(raw_data, list):
            raise MarketDataInvalidResponseError(
                f"Tiingo returned non-list for {ticker!r} bars (got {type(raw_data).__name__})"
            )
        bars: list[DailyBar] = []
        for item in raw_data:
            raw = TiingoBarResponse.model_validate(item)
            bars.append(map_tiingo_bar(raw))
        logger.debug(
            "Tiingo %r: %d bars for %s–%s",
            resolved,
            len(bars),
            start_date,
            end_date,
        )
        return bars
