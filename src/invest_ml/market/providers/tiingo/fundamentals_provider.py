"""TiingoFundamentalsProvider — implements MarketCapitalizationProvider."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from invest_ml.market.models import EquityInstrument, MarketCapitalizationObservation
from invest_ml.market.providers.tiingo.client import TiingoHttpClient
from invest_ml.market.providers.tiingo.mapper import map_tiingo_market_cap
from invest_ml.market.providers.tiingo.models import TiingoFundamentalsRow
from invest_ml.market.providers.tiingo.symbols import SymbolResolver

logger = logging.getLogger(__name__)


@dataclass
class TiingoFundamentalsSettings:
    api_token: str
    base_url: str = "https://api.tiingo.com"
    market_cap_lookback_days: int = 10
    max_retries: int = 3
    timeout: float = 30.0


class TiingoFundamentalsProvider:
    """Market-cap provider backed by the Tiingo /tiingo/fundamentals endpoint."""

    name = "tiingo_fundamentals"

    def __init__(
        self,
        settings: TiingoFundamentalsSettings,
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

    def fetch_market_cap(
        self,
        instrument: EquityInstrument,
        *,
        as_of_date: date,
    ) -> MarketCapitalizationObservation | None:
        ticker = self._resolver.resolve(instrument)
        path = f"/tiingo/fundamentals/{ticker}/daily"

        from datetime import timedelta

        start = as_of_date - timedelta(days=self._settings.market_cap_lookback_days)
        params: dict[str, Any] = {
            "columns": "marketCap",
            "startDate": start.isoformat(),
            "endDate": as_of_date.isoformat(),
        }
        raw_data: list[dict[str, Any]] = self._client.get(path, params=params)

        if not isinstance(raw_data, list) or not raw_data:
            return None

        rows = [TiingoFundamentalsRow.model_validate(r) for r in raw_data]

        best: MarketCapitalizationObservation | None = None
        for row in rows:
            obs = map_tiingo_market_cap(row)
            if obs is None:
                continue
            if obs.observation_date > as_of_date:
                continue
            if best is None or obs.observation_date > best.observation_date:
                best = obs

        return best
