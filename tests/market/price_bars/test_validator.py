"""Tests for EOD bar validation."""

from datetime import date
from decimal import Decimal

from invest_ml.market.models import DailyBar
from invest_ml.market.price_bars.validator import validate_bars

_START = date(2026, 1, 2)
_END = date(2026, 1, 31)
_TICKER = "AAPL"

_D = Decimal


def _bar(
    trading_date: date = date(2026, 1, 5),
    *,
    open: Decimal = _D("150"),
    high: Decimal = _D("155"),
    low: Decimal = _D("148"),
    close: Decimal = _D("152"),
    volume: Decimal = _D("1000000"),
    adjusted_open: Decimal = _D("150"),
    adjusted_high: Decimal = _D("155"),
    adjusted_low: Decimal = _D("148"),
    adjusted_close: Decimal = _D("152"),
    adjusted_volume: Decimal | None = _D("1000000"),
    dividend_cash: Decimal | None = _D("0"),
    split_factor: Decimal | None = _D("1"),
) -> DailyBar:
    return DailyBar(
        trading_date=trading_date,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        adjusted_open=adjusted_open,
        adjusted_high=adjusted_high,
        adjusted_low=adjusted_low,
        adjusted_close=adjusted_close,
        adjusted_volume=adjusted_volume,
        dividend_cash=dividend_cash,
        split_factor=split_factor,
    )


def test_valid_bar_passes():
    valid, rejections = validate_bars([_bar()], ticker=_TICKER, start_date=_START, end_date=_END)
    assert len(valid) == 1
    assert rejections == []


def test_date_outside_range_rejected():
    bar = _bar(trading_date=date(2025, 12, 31))
    valid, rejections = validate_bars([bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []
    assert len(rejections) == 1
    assert "outside requested range" in rejections[0]


def test_high_less_than_low_rejected():
    bar = _bar(high=_D("100"), low=_D("110"))
    valid, rejections = validate_bars([bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []
    assert any("high" in r and "low" in r for r in rejections)


def test_negative_price_rejected():
    bar = _bar(close=_D("-1"))
    valid, rejections = validate_bars([bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []


def test_negative_volume_rejected():
    bar = _bar(volume=_D("-1"))
    valid, rejections = validate_bars([bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []
    assert any("negative volume" in r for r in rejections)


def test_zero_volume_not_rejected():
    bar = _bar(volume=_D("0"))
    valid, rejections = validate_bars([bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert len(valid) == 1


def test_close_outside_high_low_rejected():
    bar = _bar(open=_D("150"), high=_D("155"), low=_D("148"), close=_D("160"))
    valid, rejections = validate_bars([bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []
    assert any("close" in r and "outside" in r for r in rejections)


def test_invalid_split_factor_zero_rejected():
    bar = _bar(split_factor=_D("0"))
    valid, rejections = validate_bars([bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []
    assert any("split_factor" in r for r in rejections)


def test_invalid_split_factor_negative_rejected():
    bar = _bar(split_factor=_D("-2"))
    valid, rejections = validate_bars([bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []


def test_empty_input_returns_empty():
    valid, rejections = validate_bars([], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []
    assert rejections == []


def test_duplicate_identical_dates_collapsed():
    bar = _bar()
    valid, rejections = validate_bars([bar, bar], ticker=_TICKER, start_date=_START, end_date=_END)
    assert len(valid) == 1
    assert rejections == []


def test_conflicting_duplicate_dates_reject_entire_response():
    bar1 = _bar(close=_D("152"))
    bar2 = _bar(close=_D("153"))  # same date, different close
    valid, rejections = validate_bars([bar1, bar2], ticker=_TICKER, start_date=_START, end_date=_END)
    assert valid == []
    assert any("conflicting duplicate" in r for r in rejections)


def test_multiple_valid_bars_sorted_by_date():
    bars = [
        _bar(date(2026, 1, 5)),
        _bar(date(2026, 1, 10)),
        _bar(date(2026, 1, 7)),
    ]
    valid, _ = validate_bars(bars, ticker=_TICKER, start_date=_START, end_date=_END)
    dates = [b.trading_date for b in valid]
    assert dates == sorted(dates)


def test_mixed_valid_and_invalid_bars():
    good = _bar(date(2026, 1, 5))
    bad = _bar(date(2026, 1, 6), high=_D("100"), low=_D("200"))
    valid, rejections = validate_bars([good, bad], ticker=_TICKER, start_date=_START, end_date=_END)
    assert len(valid) == 1
    assert valid[0].trading_date == date(2026, 1, 5)
    assert len(rejections) == 1
