"""Tests for TiingoDailyPriceProvider."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from invest_ml.market.errors import (
    MarketDataInstrumentNotFoundError,
    MarketDataInvalidResponseError,
)
from invest_ml.market.providers.tiingo.daily_provider import TiingoDailyPriceProvider


def _provider(responses: list, symbol_overrides=None) -> TiingoDailyPriceProvider:
    client = MagicMock()
    client.get.side_effect = responses
    return TiingoDailyPriceProvider(http_client=client, symbol_overrides=symbol_overrides)


def _meta_response(end_date: str = "2026-07-10") -> dict:
    return {"ticker": "SPY", "name": "SPDR", "exchangeCode": "NYSE", "startDate": "1993-01-29", "endDate": end_date}


def _bar_response(trading_date: str = "2026-07-10T00:00:00+00:00") -> dict:
    return {
        "date": trading_date,
        "open": 550.0,
        "high": 555.0,
        "low": 548.0,
        "close": 553.0,
        "volume": 80000000,
        "adjOpen": 550.0,
        "adjHigh": 555.0,
        "adjLow": 548.0,
        "adjClose": 553.0,
        "adjVolume": 80000000,
        "divCash": 0.0,
        "splitFactor": 1.0,
    }


# ── get_latest_available_date ─────────────────────────────────────────────────


def test_watermark_returns_end_date_from_metadata():
    provider = _provider([_meta_response("2026-07-10")])
    result = provider.get_latest_available_date(reference_ticker="SPY")
    assert result == date(2026, 7, 10)


def test_watermark_parses_iso_datetime_format():
    provider = _provider([_meta_response("2026-07-10T00:00:00+00:00")])
    result = provider.get_latest_available_date(reference_ticker="SPY")
    assert result == date(2026, 7, 10)


def test_watermark_raises_on_missing_end_date():
    provider = _provider([{"ticker": "SPY", "name": "SPDR", "exchangeCode": "NYSE", "startDate": "1993-01-29", "endDate": None}])
    with pytest.raises(MarketDataInvalidResponseError, match="missing endDate"):
        provider.get_latest_available_date(reference_ticker="SPY")


def test_watermark_uses_metadata_endpoint_not_price_endpoint():
    """get_latest_available_date must use /tiingo/daily/{ticker} not /prices."""
    client = MagicMock()
    client.get.return_value = _meta_response()
    provider = TiingoDailyPriceProvider(http_client=client)
    provider.get_latest_available_date(reference_ticker="SPY")

    call_args = client.get.call_args
    path = call_args[0][0]
    assert path == "/tiingo/daily/SPY"
    assert "prices" not in path


def test_watermark_applies_symbol_override():
    client = MagicMock()
    client.get.return_value = _meta_response()
    provider = TiingoDailyPriceProvider(
        http_client=client, symbol_overrides={"BRK.B": "BRK-B"}
    )
    provider.get_latest_available_date(reference_ticker="BRK.B")
    path = client.get.call_args[0][0]
    assert "BRK-B" in path
    assert "BRK.B" not in path


# ── get_daily_bars ────────────────────────────────────────────────────────────


def test_get_daily_bars_maps_fields_correctly():
    provider = _provider([[_bar_response("2026-07-10T00:00:00+00:00")]])
    bars = provider.get_daily_bars(
        ticker="SPY", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10)
    )
    assert len(bars) == 1
    bar = bars[0]
    assert bar.trading_date == date(2026, 7, 10)
    assert bar.close == Decimal("553.0")
    assert bar.adjusted_close == Decimal("553.0")
    assert bar.dividend_cash == Decimal("0.0")
    assert bar.split_factor == Decimal("1.0")


def test_get_daily_bars_returns_empty_for_empty_response():
    provider = _provider([[]])
    bars = provider.get_daily_bars(
        ticker="SPY", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10)
    )
    assert bars == []


def test_get_daily_bars_raises_for_non_list_response():
    provider = _provider([{"error": "something"}])
    with pytest.raises(MarketDataInvalidResponseError):
        provider.get_daily_bars(
            ticker="AAPL", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10)
        )


def test_get_daily_bars_uses_prices_endpoint():
    client = MagicMock()
    client.get.return_value = [_bar_response()]
    provider = TiingoDailyPriceProvider(http_client=client)
    provider.get_daily_bars(ticker="AAPL", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10))

    path = client.get.call_args[0][0]
    assert "/prices" in path
    assert "AAPL" in path


def test_get_daily_bars_sends_correct_date_params():
    client = MagicMock()
    client.get.return_value = [_bar_response()]
    provider = TiingoDailyPriceProvider(http_client=client)
    provider.get_daily_bars(ticker="AAPL", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10))

    params = client.get.call_args[1]["params"]
    assert params["startDate"] == "2026-07-01"
    assert params["endDate"] == "2026-07-10"


def test_get_daily_bars_does_not_call_metadata_endpoint():
    """No per-ticker metadata call during bar ingestion."""
    client = MagicMock()
    client.get.return_value = [_bar_response()]
    provider = TiingoDailyPriceProvider(http_client=client)
    provider.get_daily_bars(ticker="AAPL", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10))

    assert client.get.call_count == 1
    path = client.get.call_args[0][0]
    assert "/prices" in path  # only the prices endpoint was called


def test_get_daily_bars_applies_symbol_override():
    client = MagicMock()
    client.get.return_value = [_bar_response()]
    provider = TiingoDailyPriceProvider(
        http_client=client, symbol_overrides={"BRK.B": "BRK-B"}
    )
    provider.get_daily_bars(ticker="BRK.B", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10))
    path = client.get.call_args[0][0]
    assert "BRK-B" in path


def test_404_propagates_as_instrument_not_found():

    client = MagicMock()
    client.get.side_effect = MarketDataInstrumentNotFoundError("404")
    provider = TiingoDailyPriceProvider(http_client=client)

    with pytest.raises(MarketDataInstrumentNotFoundError):
        provider.get_daily_bars(
            ticker="FAKEX99", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10)
        )


def test_api_token_not_in_logged_path(caplog):
    """The Tiingo API token must never appear in log output."""
    import logging

    client = MagicMock()
    client.get.return_value = [_bar_response()]
    provider = TiingoDailyPriceProvider(http_client=client)
    with caplog.at_level(logging.DEBUG):
        provider.get_daily_bars(
            ticker="AAPL", start_date=date(2026, 7, 1), end_date=date(2026, 7, 10)
        )
    for record in caplog.records:
        assert "token" not in record.message.lower()
        assert "authorization" not in record.message.lower()
