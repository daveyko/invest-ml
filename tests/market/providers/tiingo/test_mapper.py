"""Unit tests for Tiingo mapper functions."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from invest_ml.market.models import EquityInstrument
from invest_ml.market.providers.tiingo.mapper import (
    map_tiingo_bar,
    map_tiingo_market_cap,
    map_tiingo_metadata,
)
from invest_ml.market.providers.tiingo.models import (
    TiingoBarResponse,
    TiingoFundamentalsRow,
    TiingoMetadataResponse,
)


def _instrument(ticker: str = "ACME") -> EquityInstrument:
    return EquityInstrument(
        security_id=uuid4(),
        company_id=uuid4(),
        ticker=ticker,
        exchange="NYSE",
    )


def test_map_metadata_basic():
    meta = TiingoMetadataResponse(
        ticker="ACME",
        name="Acme Corp",
        exchangeCode="NYSE",
        startDate="2010-01-04T00:00:00+00:00",
        endDate="2026-07-10T00:00:00+00:00",
    )
    result = map_tiingo_metadata(_instrument(), meta)
    assert result.canonical_ticker == "ACME"
    assert result.provider_ticker == "ACME"
    assert result.provider_start_date == date(2010, 1, 4)
    assert result.provider_end_date == date(2026, 7, 10)
    assert result.provider_name == "Acme Corp"
    assert result.provider_exchange == "NYSE"


def test_map_metadata_null_dates():
    meta = TiingoMetadataResponse(ticker="ZZZZ", startDate=None, endDate=None)
    result = map_tiingo_metadata(_instrument("ZZZZ"), meta)
    assert result.provider_start_date is None
    assert result.provider_end_date is None


def test_map_bar_full():
    raw = TiingoBarResponse(
        date="2026-07-10T00:00:00+00:00",
        open=100.0,
        high=105.0,
        low=98.0,
        close=103.0,
        volume=1_000_000.0,
        adjOpen=99.0,
        adjHigh=104.0,
        adjLow=97.0,
        adjClose=102.0,
        adjVolume=1_000_000.0,
        divCash=0.5,
        splitFactor=1.0,
    )
    bar = map_tiingo_bar(raw)
    assert bar.trading_date == date(2026, 7, 10)
    assert bar.close == Decimal("103.0")
    assert bar.adjusted_close == Decimal("102.0")
    assert bar.dividend_cash == Decimal("0.5")
    assert bar.split_factor == Decimal("1.0")


def test_map_bar_none_optional_fields():
    raw = TiingoBarResponse(date="2026-01-02T00:00:00+00:00", close=50.0)
    bar = map_tiingo_bar(raw)
    assert bar.open is None
    assert bar.volume is None
    assert bar.adjusted_close is None
    assert bar.dividend_cash is None


def test_map_market_cap_success():
    row = TiingoFundamentalsRow(date="2026-07-01T00:00:00+00:00", marketCap=5_000_000_000.0)
    obs = map_tiingo_market_cap(row)
    assert obs is not None
    assert obs.observation_date == date(2026, 7, 1)
    assert obs.market_cap == Decimal("5000000000.0")
    assert obs.currency == "USD"


def test_map_market_cap_none():
    row = TiingoFundamentalsRow(date="2026-07-01T00:00:00+00:00", marketCap=None)
    obs = map_tiingo_market_cap(row)
    assert obs is None
