"""Market profile calculator.

Uses exchange-calendars for accurate trading session counting.
The calendar_factory is injectable so tests can run without the real library.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from invest_ml.market.models import DailyBar, HistoricalBars


@dataclass(frozen=True)
class MarketProfileCalculationConfig:
    liquidity_lookback_sessions: int = 90
    missing_ratio_lookback_years: int = 3
    history_lookback_years: int = 3
    default_exchange_calendar: str = "XNYS"


@dataclass
class CalculatedMarketProfile:
    first_price_date: date | None
    latest_price_date: date | None
    price_history_years: float | None
    history_truncated_by_requested_window: bool
    median_daily_dollar_volume: float | None
    missing_trading_day_ratio: float | None
    latest_adjusted_close: float | None
    quality_flags: dict[str, Any] = field(default_factory=dict)
    status: str = "success"


_EXCHANGE_CALENDAR_MAP: dict[str, str] = {
    "NYSE": "XNYS",
    "Nasdaq": "XNYS",
    "NASDAQ": "XNYS",
    "NYSE American": "XASE",
    "NYSE MKT": "XASE",
}


def _default_calendar_factory(exchange: str | None, default: str) -> Any:
    import exchange_calendars as ec

    xcode = _EXCHANGE_CALENDAR_MAP.get(exchange or "", default)
    return ec.get_calendar(xcode)


def _valid_bars(bars: tuple[DailyBar, ...]) -> list[DailyBar]:
    return [
        b
        for b in bars
        if b.close is not None and b.close > Decimal("0")
    ]


class MarketProfileCalculator:
    def __init__(
        self,
        *,
        calendar_factory: Callable[[str | None, str], Any] | None = None,
    ) -> None:
        self._calendar_factory = calendar_factory or _default_calendar_factory

    def calculate(
        self,
        history: HistoricalBars,
        *,
        as_of_date: date,
        config: MarketProfileCalculationConfig,
    ) -> CalculatedMarketProfile:
        bars = _valid_bars(history.bars)

        if not bars:
            return CalculatedMarketProfile(
                first_price_date=None,
                latest_price_date=None,
                price_history_years=None,
                history_truncated_by_requested_window=False,
                median_daily_dollar_volume=None,
                missing_trading_day_ratio=None,
                latest_adjusted_close=None,
                quality_flags={"status": "no_usable_bars"},
                status="no_usable_bars",
            )

        bars_sorted = sorted(bars, key=lambda b: b.trading_date)
        latest_bar = bars_sorted[-1]
        latest_price_date = latest_bar.trading_date

        metadata = history.asset_metadata
        first_price_date = metadata.provider_start_date

        # price_history_years spans from metadata startDate to latest observed bar
        if first_price_date is not None:
            delta_days = (latest_price_date - first_price_date).days
            price_history_years: float | None = delta_days / 365.2425
        else:
            price_history_years = None

        # Was full history truncated because we only fetched 3 years?
        from datetime import timedelta

        bar_request_start = as_of_date - timedelta(
            days=int(config.history_lookback_years * 365.2425)
        )
        history_truncated = (
            first_price_date is not None and first_price_date < bar_request_start
        )

        # Median daily dollar volume over last N sessions
        mdv = self._median_dollar_volume(bars_sorted, config.liquidity_lookback_sessions)

        # Missing trading day ratio
        exchange = history.instrument.exchange if history.instrument else None
        mtr = self._missing_trading_day_ratio(
            bars_sorted,
            as_of_date=as_of_date,
            lookback_years=config.missing_ratio_lookback_years,
            exchange=exchange,
            default_calendar=config.default_exchange_calendar,
        )

        latest_adj_close = (
            float(latest_bar.adjusted_close)
            if latest_bar.adjusted_close is not None
            else float(latest_bar.close)
        )

        return CalculatedMarketProfile(
            first_price_date=first_price_date,
            latest_price_date=latest_price_date,
            price_history_years=price_history_years,
            history_truncated_by_requested_window=history_truncated,
            median_daily_dollar_volume=mdv,
            missing_trading_day_ratio=mtr,
            latest_adjusted_close=latest_adj_close,
            quality_flags={
                "status": "success",
                "history_truncated": history_truncated,
                "bars_fetched": len(history.bars),
                "bars_valid": len(bars),
            },
            status="success",
        )

    def _median_dollar_volume(
        self,
        bars_sorted: list[DailyBar],
        lookback_sessions: int,
    ) -> float | None:
        recent = bars_sorted[-lookback_sessions:]
        dollar_vols = []
        for bar in recent:
            if bar.volume is not None and bar.volume > Decimal("0"):
                dollar_vols.append(float(bar.close * bar.volume))
        if not dollar_vols:
            return None
        return statistics.median(dollar_vols)

    def _missing_trading_day_ratio(
        self,
        bars_sorted: list[DailyBar],
        *,
        as_of_date: date,
        lookback_years: int,
        exchange: str | None,
        default_calendar: str,
    ) -> float | None:
        from datetime import timedelta

        import pandas as pd

        lookback_start = as_of_date - timedelta(days=int(lookback_years * 365.2425))
        window_bars = [b for b in bars_sorted if b.trading_date >= lookback_start]

        try:
            cal = self._calendar_factory(exchange, default_calendar)
            sessions = cal.sessions_in_range(
                pd.Timestamp(lookback_start),
                pd.Timestamp(as_of_date),
            )
        except Exception:
            return None

        if len(sessions) == 0:
            return None

        bar_dates = {b.trading_date for b in window_bars}
        session_dates = {s.date() for s in sessions}
        missing = len(session_dates - bar_dates)
        return missing / len(session_dates)
