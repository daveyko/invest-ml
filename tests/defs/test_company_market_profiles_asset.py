"""Tests for the company_market_profiles Dagster asset.

Mocks CompanyMarketProfileService and providers — no DB or Tiingo requests.
"""

from unittest.mock import MagicMock, patch

from dagster import build_asset_context

from invest_ml.defs.assets.discovery import company_market_profiles
from invest_ml.market.service import CompanyMarketProfileResult

_SERVICE_PATH = "invest_ml.market.service.CompanyMarketProfileService"
_LOAD_MARKET = "invest_ml.config.loaders.load_market_data_config"


def _make_postgres_resource() -> MagicMock:
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    resource = MagicMock()
    resource.get_session_factory.return_value = MagicMock(return_value=mock_session)
    return resource


def _make_equity_resource() -> MagicMock:
    resource = MagicMock()
    resource.maximum_symbols_per_run = 100
    resource.build_price_provider.return_value = MagicMock()
    resource.build_market_cap_provider.return_value = None
    return resource


def _market_config() -> dict:
    return {
        "market_data": {"provider": "tiingo", "symbol_overrides": {}},
        "market_profiles": {
            "profile_version": "market_profile_v1",
            "universe_name": "candidate",
            "universe_version": "v1",
            "history_lookback_years": 3,
            "refresh_after_days": 30,
            "failed_symbol_retry_after_days": 30,
            "liquidity_lookback_sessions": 90,
            "missing_ratio_lookback_years": 3,
            "maximum_symbols_per_run": 100,
        },
    }


def _mock_result() -> CompanyMarketProfileResult:
    return CompanyMarketProfileResult(
        targets_found=50,
        profiles_succeeded=45,
        profiles_not_found=3,
        profiles_temporary_failure=2,
        market_cap_disabled=True,
        metadata_requests=50,
        price_requests=47,
        market_cap_requests=0,
    )


def test_asset_happy_path():
    pg = _make_postgres_resource()
    equity = _make_equity_resource()

    with (
        patch(_LOAD_MARKET, return_value=_market_config()),
        patch(_SERVICE_PATH) as MockService,
    ):
        service_inst = MockService.return_value
        service_inst.materialize.return_value = _mock_result()

        ctx = build_asset_context()
        result = company_market_profiles(ctx, postgres=pg, equity_market_data=equity)

    assert result is not None
    meta = result.metadata
    assert int(meta["targets_found"].value) == 50
    assert int(meta["profiles_succeeded"].value) == 45
    assert int(meta["profiles_not_found"].value) == 3
    assert int(meta["profiles_temporary_failure"].value) == 2
    service_inst.materialize.assert_called_once()


def test_asset_config_passes_correct_universe():
    pg = _make_postgres_resource()
    equity = _make_equity_resource()

    with (
        patch(_LOAD_MARKET, return_value=_market_config()),
        patch(_SERVICE_PATH) as MockService,
    ):
        service_inst = MockService.return_value
        service_inst.materialize.return_value = _mock_result()

        ctx = build_asset_context()
        company_market_profiles(ctx, postgres=pg, equity_market_data=equity)

    call_kwargs = service_inst.materialize.call_args.kwargs
    config = call_kwargs["config"]
    assert config.universe_name == "candidate"
    assert config.universe_version == "v1"
    assert config.profile_version == "market_profile_v1"


def test_asset_uses_no_market_cap_provider_when_disabled():
    pg = _make_postgres_resource()
    equity = _make_equity_resource()

    with (
        patch(_LOAD_MARKET, return_value=_market_config()),
        patch(_SERVICE_PATH) as MockService,
    ):
        service_inst = MockService.return_value
        service_inst.materialize.return_value = _mock_result()

        ctx = build_asset_context()
        company_market_profiles(ctx, postgres=pg, equity_market_data=equity)

    equity.build_market_cap_provider.assert_called_once()


def test_asset_passes_symbol_overrides_to_providers():
    pg = _make_postgres_resource()
    equity = _make_equity_resource()
    cfg = _market_config()
    cfg["market_data"]["symbol_overrides"] = {"BRK-B": "BRK.B"}

    with (
        patch(_LOAD_MARKET, return_value=cfg),
        patch(_SERVICE_PATH) as MockService,
    ):
        service_inst = MockService.return_value
        service_inst.materialize.return_value = _mock_result()

        ctx = build_asset_context()
        company_market_profiles(ctx, postgres=pg, equity_market_data=equity)

    equity.build_price_provider.assert_called_once_with({"BRK-B": "BRK.B"})
    equity.build_market_cap_provider.assert_called_once_with({"BRK-B": "BRK.B"})
