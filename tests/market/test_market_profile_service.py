"""Unit tests for CompanyMarketProfileService.

All DB and provider calls are mocked.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from invest_ml.market.errors import (
    MarketDataAuthenticationError,
    MarketDataInstrumentNotFoundError,
    MarketDataTemporaryError,
)
from invest_ml.market.models import AssetMetadata, DailyBar, EquityInstrument, HistoricalBars
from invest_ml.market.profile import (
    CalculatedMarketProfile,
    MarketProfileCalculator,
)
from invest_ml.market.service import (
    CompanyMarketProfileService,
    MarketProfileRunConfig,
)

AS_OF = date(2026, 7, 10)

_REPO_PATH = "invest_ml.market.service.CompanyMarketProfileRepository"


def _run_config() -> MarketProfileRunConfig:
    return MarketProfileRunConfig(
        universe_name="candidate",
        universe_version="v1",
        profile_version="market_profile_v1",
        maximum_symbols_per_run=10,
    )


def _target(ticker: str = "ACME") -> MagicMock:
    t = MagicMock()
    t.company_id = uuid4()
    t.security_id = uuid4()
    t.ticker = ticker
    t.exchange = "NYSE"
    return t


def _history(bars_count: int = 5) -> HistoricalBars:
    instrument = EquityInstrument(
        security_id=uuid4(), company_id=uuid4(), ticker="ACME", exchange="NYSE"
    )
    bars = tuple(
        DailyBar(
            trading_date=AS_OF - timedelta(days=i),
            open=None, high=None, low=None,
            close=Decimal("100.0"),
            volume=Decimal("1000000"),
            adjusted_open=None, adjusted_high=None, adjusted_low=None,
            adjusted_close=Decimal("99.0"),
            adjusted_volume=None,
            dividend_cash=None,
            split_factor=None,
        )
        for i in range(bars_count)
    )
    meta = AssetMetadata(
        canonical_ticker="ACME",
        provider_ticker="ACME",
        provider_name=None,
        provider_exchange="NYSE",
        provider_start_date=date(2010, 1, 4),
        provider_end_date=AS_OF,
        metadata={},
    )
    return HistoricalBars(
        instrument=instrument,
        asset_metadata=meta,
        bars=bars,
        currency="USD",
        adjustment_method="split_and_dividend_adjusted",
        provider_metadata={},
    )


def _session_factory() -> MagicMock:
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=mock_session)


def _mock_calculator() -> MarketProfileCalculator:
    calc = MagicMock(spec=MarketProfileCalculator)
    calc.calculate.return_value = CalculatedMarketProfile(
        first_price_date=date(2010, 1, 4),
        latest_price_date=AS_OF,
        price_history_years=16.5,
        history_truncated_by_requested_window=True,
        median_daily_dollar_volume=100_000_000.0,
        missing_trading_day_ratio=0.02,
        latest_adjusted_close=99.0,
        quality_flags={"status": "success"},
        status="success",
    )
    return calc


def test_service_succeeds_for_target():
    price_provider = MagicMock()
    price_provider.adapter_version = "tiingo_eod_v1"
    price_provider.fetch_daily_bars.return_value = _history()

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.list_market_profile_targets.return_value = [_target()]
        inst.upsert_profile.return_value = None

        service = CompanyMarketProfileService(
            session_factory=_session_factory(),
            price_provider=price_provider,
            calculator=_mock_calculator(),
        )
        result = service.materialize(as_of_date=AS_OF, config=_run_config())

    assert result.profiles_succeeded == 1
    assert result.profiles_not_found == 0
    assert result.price_requests == 1
    assert result.metadata_requests == 1


def test_instrument_not_found_increments_counter():
    price_provider = MagicMock()
    price_provider.adapter_version = "tiingo_eod_v1"
    price_provider.fetch_daily_bars.side_effect = MarketDataInstrumentNotFoundError("not found")

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.list_market_profile_targets.return_value = [_target()]
        inst.upsert_profile.return_value = None

        service = CompanyMarketProfileService(
            session_factory=_session_factory(),
            price_provider=price_provider,
            calculator=_mock_calculator(),
        )
        result = service.materialize(as_of_date=AS_OF, config=_run_config())

    assert result.profiles_not_found == 1
    assert result.profiles_succeeded == 0


def test_temporary_failure_increments_counter():
    price_provider = MagicMock()
    price_provider.adapter_version = "tiingo_eod_v1"
    price_provider.fetch_daily_bars.side_effect = MarketDataTemporaryError("server down")

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.list_market_profile_targets.return_value = [_target()]
        inst.upsert_profile.return_value = None

        service = CompanyMarketProfileService(
            session_factory=_session_factory(),
            price_provider=price_provider,
            calculator=_mock_calculator(),
        )
        result = service.materialize(as_of_date=AS_OF, config=_run_config())

    assert result.profiles_temporary_failure == 1
    assert result.profiles_succeeded == 0


def test_authentication_error_propagates():
    price_provider = MagicMock()
    price_provider.adapter_version = "tiingo_eod_v1"
    price_provider.fetch_daily_bars.side_effect = MarketDataAuthenticationError("bad token")

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.list_market_profile_targets.return_value = [_target()]

        service = CompanyMarketProfileService(
            session_factory=_session_factory(),
            price_provider=price_provider,
            calculator=_mock_calculator(),
        )
        with pytest.raises(MarketDataAuthenticationError):
            service.materialize(as_of_date=AS_OF, config=_run_config())


def test_no_targets_returns_zero_counts():
    price_provider = MagicMock()
    price_provider.adapter_version = "tiingo_eod_v1"

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.list_market_profile_targets.return_value = []

        service = CompanyMarketProfileService(
            session_factory=_session_factory(),
            price_provider=price_provider,
        )
        result = service.materialize(as_of_date=AS_OF, config=_run_config())

    assert result.targets_found == 0
    assert result.profiles_succeeded == 0
    assert result.price_requests == 0
