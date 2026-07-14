"""Unit tests for TiingoEodProvider.

All tests use mocked TiingoHttpClient — no live Tiingo requests.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from invest_ml.market.models import EquityInstrument
from invest_ml.market.providers.tiingo.eod_provider import TiingoEodProvider, TiingoEodSettings


def _instrument(ticker: str = "ACME") -> EquityInstrument:
    return EquityInstrument(
        security_id=uuid4(),
        company_id=uuid4(),
        ticker=ticker,
        exchange="NYSE",
    )


def _eod_settings() -> TiingoEodSettings:
    return TiingoEodSettings(api_token="test-token")


def _mock_http_client() -> MagicMock:
    return MagicMock()


def test_fetch_asset_metadata_success():
    client = _mock_http_client()
    client.get.return_value = {
        "ticker": "ACME",
        "name": "Acme Corp",
        "exchangeCode": "NYSE",
        "startDate": "2010-01-04T00:00:00+00:00",
        "endDate": "2026-07-10T00:00:00+00:00",
    }
    provider = TiingoEodProvider(_eod_settings(), http_client=client)
    meta = provider.fetch_asset_metadata(_instrument())
    assert meta.provider_start_date == date(2010, 1, 4)
    assert meta.provider_ticker == "ACME"


def test_fetch_daily_bars_success():
    meta_resp = {
        "ticker": "ACME",
        "exchangeCode": "NYSE",
        "startDate": "2010-01-04T00:00:00+00:00",
        "endDate": "2026-07-10T00:00:00+00:00",
    }
    bars_resp = [
        {
            "date": "2026-07-10T00:00:00+00:00",
            "close": 100.0,
            "adjClose": 99.5,
            "volume": 500_000.0,
        }
    ]
    client = _mock_http_client()
    client.get.side_effect = [bars_resp, meta_resp]

    provider = TiingoEodProvider(_eod_settings(), http_client=client)
    history = provider.fetch_daily_bars(
        _instrument(), start_date=date(2023, 7, 10), end_date=date(2026, 7, 10)
    )
    assert len(history.bars) == 1
    assert history.bars[0].close == Decimal("100.0")
    assert history.bars[0].adjusted_close == Decimal("99.5")


def test_fetch_daily_bars_returns_empty_list():
    client = _mock_http_client()
    client.get.side_effect = [[], {"ticker": "ACME", "exchangeCode": "NYSE"}]
    provider = TiingoEodProvider(_eod_settings(), http_client=client)
    history = provider.fetch_daily_bars(
        _instrument(), start_date=date(2023, 7, 10), end_date=date(2026, 7, 10)
    )
    assert history.bars == ()


def test_symbol_override_applied():
    client = _mock_http_client()
    client.get.return_value = {
        "ticker": "BRK.B",
        "exchangeCode": "NYSE",
        "startDate": "1996-01-02T00:00:00+00:00",
    }
    provider = TiingoEodProvider(
        _eod_settings(), symbol_overrides={"BRK-B": "BRK.B"}, http_client=client
    )
    instrument = EquityInstrument(
        security_id=uuid4(), company_id=uuid4(), ticker="BRK-B", exchange="NYSE"
    )
    provider.fetch_asset_metadata(instrument)
    call_args = client.get.call_args
    assert "BRK.B" in call_args.args[0]
