"""Validate Tiingo EOD bar responses before persistence."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation

from invest_ml.market.models import DailyBar

_ZERO = Decimal("0")
_ONE = Decimal("1")


def validate_bars(
    bars: Sequence[DailyBar],
    *,
    ticker: str,
    start_date: date,
    end_date: date,
) -> tuple[list[DailyBar], list[str]]:
    """Validate a sequence of bars returned by the provider.

    Returns (valid_bars, rejection_reasons).  Duplicate dates that are
    byte-equivalent are collapsed to one row; conflicting duplicates cause
    the entire response to be rejected.
    """
    if not bars:
        return [], []

    by_date: dict[date, DailyBar] = {}
    rejections: list[str] = []

    for bar in bars:
        # Date must be present (guaranteed by DailyBar type) and in range
        if bar.trading_date < start_date or bar.trading_date > end_date:
            rejections.append(
                f"{ticker} {bar.trading_date}: date outside requested range "
                f"[{start_date}, {end_date}]"
            )
            continue

        # Duplicate date handling
        if bar.trading_date in by_date:
            existing = by_date[bar.trading_date]
            if _bars_identical(existing, bar):
                continue  # collapse equivalent duplicates
            return [], [
                f"{ticker} {bar.trading_date}: conflicting duplicate dates in response"
            ]

        # Per-bar validations
        reason = _validate_bar(bar, ticker)
        if reason:
            rejections.append(reason)
            continue

        by_date[bar.trading_date] = bar

    valid = [by_date[d] for d in sorted(by_date)]
    return valid, rejections


def _validate_bar(bar: DailyBar, ticker: str) -> str | None:
    td = bar.trading_date

    # Non-finite check (guard against NaN or Inf sneaking through)
    for name, val in (
        ("close", bar.close),
        ("open", bar.open),
        ("high", bar.high),
        ("low", bar.low),
        ("adjusted_close", bar.adjusted_close),
    ):
        if val is not None and not _is_finite(val):
            return f"{ticker} {td}: non-finite {name}={val}"

    # Negative prices
    for name, val in (
        ("close", bar.close),
        ("open", bar.open),
        ("high", bar.high),
        ("low", bar.low),
        ("adjusted_close", bar.adjusted_close),
        ("adjusted_open", bar.adjusted_open),
        ("adjusted_high", bar.adjusted_high),
        ("adjusted_low", bar.adjusted_low),
    ):
        if val is not None and val < _ZERO:
            return f"{ticker} {td}: negative {name}={val}"

    # Negative volume
    if bar.volume is not None and bar.volume < _ZERO:
        return f"{ticker} {td}: negative volume={bar.volume}"
    if bar.adjusted_volume is not None and bar.adjusted_volume < _ZERO:
        return f"{ticker} {td}: negative adjusted_volume={bar.adjusted_volume}"

    # high >= low
    if bar.high is not None and bar.low is not None and bar.high < bar.low:
        return f"{ticker} {td}: high={bar.high} < low={bar.low}"
    if (
        bar.adjusted_high is not None
        and bar.adjusted_low is not None
        and bar.adjusted_high < bar.adjusted_low
    ):
        return f"{ticker} {td}: adjusted_high={bar.adjusted_high} < adjusted_low={bar.adjusted_low}"

    # open/close within [low, high] (with small tolerance for rounding)
    _TOL = Decimal("0.001")
    if bar.high is not None and bar.low is not None:
        if bar.open is not None:
            if bar.open > bar.high + _TOL or bar.open < bar.low - _TOL:
                return f"{ticker} {td}: open={bar.open} outside [low={bar.low}, high={bar.high}]"
        if bar.close > bar.high + _TOL or bar.close < bar.low - _TOL:
            return f"{ticker} {td}: close={bar.close} outside [low={bar.low}, high={bar.high}]"

    # Invalid split factor (must be positive)
    if bar.split_factor is not None and bar.split_factor <= _ZERO:
        return f"{ticker} {td}: invalid split_factor={bar.split_factor}"

    return None


def _is_finite(value: Decimal) -> bool:
    try:
        f = float(value)
        return math.isfinite(f)
    except (InvalidOperation, OverflowError):
        return False


def _bars_identical(a: DailyBar, b: DailyBar) -> bool:
    return (
        a.open == b.open
        and a.high == b.high
        and a.low == b.low
        and a.close == b.close
        and a.volume == b.volume
        and a.adjusted_open == b.adjusted_open
        and a.adjusted_high == b.adjusted_high
        and a.adjusted_low == b.adjusted_low
        and a.adjusted_close == b.adjusted_close
        and a.adjusted_volume == b.adjusted_volume
        and a.dividend_cash == b.dividend_cash
        and a.split_factor == b.split_factor
    )
