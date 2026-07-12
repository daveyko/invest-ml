"""Unit tests for EligibleSecuritySelector."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from invest_ml.universe.security_selector import EligibleSecurityInput, EligibleSecuritySelector

_PROFILE_VERSION = "market_profile_v1"
_AS_OF = date(2025, 6, 1)
_MAX_AGE = 45


def _make_security(
    *,
    ticker: str = "AAPL",
    currently_observed: bool = True,
    profile_version: str = _PROFILE_VERSION,
    status: str = "success",
    scanned_days_ago: int = 0,
    price_history_years: Decimal | None = Decimal("5"),
    median_daily_dollar_volume: Decimal | None = Decimal("10_000_000"),
    missing_trading_day_ratio: Decimal | None = Decimal("0.01"),
    latest_adjusted_close: Decimal | None = Decimal("150.00"),
    current_market_cap: Decimal | None = None,
    security_id=None,
    company_id=None,
) -> EligibleSecurityInput:
    scanned_at = datetime(2025, 5, 31, tzinfo=UTC) - timedelta(days=scanned_days_ago)
    return EligibleSecurityInput(
        security_id=security_id or uuid4(),
        company_id=company_id or uuid4(),
        ticker=ticker,
        exchange="Nasdaq",
        currently_observed=currently_observed,
        market_profile_version=profile_version,
        market_profile_scanned_at=scanned_at,
        market_profile_status=status,
        first_price_date=date(2018, 1, 1),
        latest_price_date=date(2025, 5, 31),
        price_history_years=price_history_years,
        median_daily_dollar_volume=median_daily_dollar_volume,
        current_market_cap=current_market_cap,
        missing_trading_day_ratio=missing_trading_day_ratio,
        latest_adjusted_close=latest_adjusted_close,
    )


_SELECTOR = EligibleSecuritySelector()


def _select(*secs, as_of=_AS_OF, max_age=_MAX_AGE):
    return _SELECTOR.select(
        list(secs),
        profile_version=_PROFILE_VERSION,
        as_of_date=as_of,
        maximum_profile_age_days=max_age,
    )


def test_select_single_eligible_returns_it():
    sec = _make_security()
    result = _select(sec)
    assert result.selected_security is sec
    assert result.selection_reasons["selection_method"] == "only_eligible_security"


def test_select_empty_returns_none():
    result = _select()
    assert result.selected_security is None
    assert result.considered_security_ids == ()


def test_not_observed_excluded():
    sec = _make_security(currently_observed=False)
    result = _select(sec)
    assert result.selected_security is None


def test_wrong_profile_version_excluded():
    sec = _make_security(profile_version="old_version")
    result = _select(sec)
    assert result.selected_security is None


def test_non_success_status_excluded():
    sec = _make_security(status="failed")
    result = _select(sec)
    assert result.selected_security is None


def test_stale_profile_excluded():
    sec = _make_security(scanned_days_ago=46)
    result = _select(sec)
    assert result.selected_security is None


def test_missing_adjusted_close_excluded():
    sec = _make_security(latest_adjusted_close=None)
    result = _select(sec)
    assert result.selected_security is None


def test_highest_liquidity_selected():
    low = _make_security(ticker="LOW", median_daily_dollar_volume=Decimal("1_000_000"))
    high = _make_security(ticker="HIGH", median_daily_dollar_volume=Decimal("5_000_000"))
    result = _select(low, high)
    assert result.selected_security is high
    assert result.selection_reasons["selection_method"] == "highest_median_daily_dollar_volume"


def test_longest_history_tiebreaker():
    short = _make_security(ticker="SH", price_history_years=Decimal("3"))
    long_ = _make_security(ticker="LO", price_history_years=Decimal("7"))
    # Same MDV
    result = _select(short, long_)
    assert result.selected_security is long_
    assert result.selection_reasons["selection_method"] == "longest_price_history"


def test_ticker_ascending_final_tiebreaker():
    # Identical MDV, history, missing ratio, scan date → ticker sort
    a = _make_security(ticker="AAA")
    b = _make_security(ticker="ZZZ")
    result = _select(a, b)
    assert result.selected_security is a
    assert "ticker_ascending" in result.selection_reasons["selection_method"]
