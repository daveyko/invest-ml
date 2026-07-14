"""DailyPriceProvider protocol for price-bar ingestion."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from invest_ml.market.models import DailyBar


class DailyPriceProvider(Protocol):
    """Minimal provider interface for EOD price-bar ingestion.

    Implementations must be thread-safe: get_daily_bars() will be called
    concurrently from a ThreadPoolExecutor.
    """

    def get_latest_available_date(self, *, reference_ticker: str) -> date:
        """Return the latest trading date the provider has published.

        Called once per materialization run to determine the target end date.
        Raises MarketDataError on failure — the caller should not proceed with
        per-ticker requests if this fails.
        """
        ...

    def get_daily_bars(
        self,
        *,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyBar]:
        """Fetch daily bars for one ticker over the given inclusive date range.

        Returns an empty sequence if no bars are available for the range.
        Raises MarketDataInstrumentNotFoundError for permanently unknown tickers.
        Raises MarketDataError subtypes for retriable or permanent failures.
        """
        ...
