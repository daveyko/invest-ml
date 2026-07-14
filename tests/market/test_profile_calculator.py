"""Unit tests for MarketProfileCalculator.

Uses injectable calendar_factory to avoid exchange_calendars runtime dependency.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from invest_ml.market.models import AssetMetadata, DailyBar, EquityInstrument, HistoricalBars
from invest_ml.market.profile import (
    MarketProfileCalculationConfig,
    MarketProfileCalculator,
)

AS_OF = date(2026, 7, 10)
_3YR_START = AS_OF - timedelta(days=int(3 * 365.2425))


def _instrument() -> EquityInstrument:
    return EquityInstrument(
        security_id=uuid4(),
        company_id=uuid4(),
        ticker="ACME",
        exchange="NYSE",
    )


def _metadata(start_date: date | None = date(2010, 1, 4)) -> AssetMetadata:
    return AssetMetadata(
        canonical_ticker="ACME",
        provider_ticker="ACME",
        provider_name="Acme Corp",
        provider_exchange="NYSE",
        provider_start_date=start_date,
        provider_end_date=AS_OF,
        metadata={},
    )


def _bar(
    d: date,
    close: float = 100.0,
    volume: float | None = 1_000_000.0,
    adj_close: float | None = None,
) -> DailyBar:
    return DailyBar(
        trading_date=d,
        open=None,
        high=None,
        low=None,
        close=Decimal(str(close)),
        volume=Decimal(str(volume)) if volume is not None else None,
        adjusted_open=None,
        adjusted_high=None,
        adjusted_low=None,
        adjusted_close=Decimal(str(adj_close)) if adj_close is not None else None,
        adjusted_volume=None,
        dividend_cash=None,
        split_factor=None,
    )


def _history(bars: list[DailyBar], start_date: date | None = date(2010, 1, 4)) -> HistoricalBars:
    return HistoricalBars(
        instrument=_instrument(),
        asset_metadata=_metadata(start_date),
        bars=tuple(bars),
        currency="USD",
        adjustment_method="split_and_dividend_adjusted",
        provider_metadata={},
    )


def _fake_calendar(n_sessions: int = 756) -> Any:
    sessions = [AS_OF - timedelta(days=i) for i in range(n_sessions)]
    cal = MagicMock()
    cal.sessions_in_range.return_value = [MagicMock(date=MagicMock(return_value=s)) for s in sessions]
    return cal


def _calculator(n_sessions: int = 756) -> MarketProfileCalculator:
    cal = _fake_calendar(n_sessions)
    return MarketProfileCalculator(calendar_factory=lambda *_: cal)


def _config() -> MarketProfileCalculationConfig:
    return MarketProfileCalculationConfig(
        liquidity_lookback_sessions=90,
        missing_ratio_lookback_years=3,
        history_lookback_years=3,
    )


def test_no_bars_returns_no_usable_bars_status():
    calc = _calculator()
    history = _history([])
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.status == "no_usable_bars"
    assert result.first_price_date is None
    assert result.latest_price_date is None


def test_basic_calculation_success():
    bars = [_bar(AS_OF - timedelta(days=i), close=100.0, volume=500_000.0) for i in range(90)]
    calc = _calculator(n_sessions=756)
    history = _history(bars)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.status == "success"
    assert result.latest_price_date == AS_OF
    assert result.median_daily_dollar_volume == pytest.approx(100.0 * 500_000.0)


def test_first_price_date_from_metadata():
    bars = [_bar(AS_OF - timedelta(days=i)) for i in range(5)]
    calc = _calculator()
    history = _history(bars, start_date=date(2010, 1, 4))
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.first_price_date == date(2010, 1, 4)


def test_price_history_years_from_metadata_start_date():
    bars = [_bar(AS_OF)]
    start = date(2023, 7, 10)
    calc = _calculator()
    history = _history(bars, start_date=start)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    expected = (AS_OF - start).days / 365.2425
    assert result.price_history_years == pytest.approx(expected, rel=1e-4)


def test_history_truncated_flag_when_metadata_start_before_request_window():
    bars = [_bar(AS_OF - timedelta(days=i)) for i in range(10)]
    start = date(2010, 1, 4)  # way before 3yr window
    calc = _calculator()
    history = _history(bars, start_date=start)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.history_truncated_by_requested_window is True


def test_history_not_truncated_when_metadata_start_within_window():
    bars = [_bar(AS_OF - timedelta(days=i)) for i in range(10)]
    start = AS_OF - timedelta(days=365)  # within 3yr window
    calc = _calculator()
    history = _history(bars, start_date=start)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.history_truncated_by_requested_window is False


def test_adjusted_close_used_for_latest():
    bars = [_bar(AS_OF, close=100.0, adj_close=98.0)]
    calc = _calculator()
    history = _history(bars)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.latest_adjusted_close == pytest.approx(98.0)


def test_close_used_when_no_adjusted_close():
    bars = [_bar(AS_OF, close=75.0, adj_close=None)]
    calc = _calculator()
    history = _history(bars)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.latest_adjusted_close == pytest.approx(75.0)


def test_zero_close_bars_excluded_from_valids():
    bars = [_bar(AS_OF, close=0.0)]
    calc = _calculator()
    history = _history(bars)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.status == "no_usable_bars"


def test_median_dollar_volume_no_volume():
    bars = [_bar(AS_OF, close=100.0, volume=None)]
    calc = _calculator()
    history = _history(bars)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.median_daily_dollar_volume is None


def test_missing_ratio_all_present():
    n_sessions = 756
    bars = [_bar(AS_OF - timedelta(days=i)) for i in range(n_sessions)]
    cal = MagicMock()
    session_dates = [AS_OF - timedelta(days=i) for i in range(n_sessions)]

    class _FakeTs:
        def __init__(self, d):
            self._d = d
        def date(self):
            return self._d

    cal.sessions_in_range.return_value = [_FakeTs(d) for d in session_dates]
    calc = MarketProfileCalculator(calendar_factory=lambda *_: cal)
    history = _history(bars)
    result = calc.calculate(
        history,
        as_of_date=AS_OF,
        config=_config(),
    )
    assert result.missing_trading_day_ratio == pytest.approx(0.0, abs=0.01)
