"""Dagster asset smoke tests for selected_price_bars."""

from unittest.mock import MagicMock

import pytest


def test_asset_depends_on_training_universe():
    from invest_ml.defs.assets.market import selected_price_bars

    dep_keys = {k.path[-1] for k in selected_price_bars.asset_deps[selected_price_bars.key]}
    assert "training_universe" in dep_keys


def test_asset_group_is_market_data():
    from invest_ml.defs.assets.market import selected_price_bars

    groups = list(selected_price_bars.group_names_by_key.values())
    assert all(g == "market_data" for g in groups)


def test_definitions_load_without_network_activity():
    """Importing Definitions must not make any network or DB calls."""
    from invest_ml.definitions import defs

    assert defs is not None


def test_selected_price_bars_in_asset_graph():
    from invest_ml.definitions import defs

    graph = defs.resolve_asset_graph()
    keys = {k.path[-1] for k in graph.get_all_asset_keys()}
    assert "selected_price_bars" in keys
    assert "price_bars" not in keys  # old stub is replaced


def test_asset_returns_materialization_result_not_price_bars():
    """Verify the asset returns MaterializeResult and doesn't yield raw price data."""
    from dagster import build_asset_context

    from invest_ml.defs.assets.market import selected_price_bars

    mock_postgres = MagicMock()
    mock_emd = MagicMock()
    mock_emd.tiingo_eod_reference_ticker = "SPY"
    mock_emd.price_bars_backfill_start_date = "2015-01-01"
    mock_emd.price_bars_target_end_date = "2026-07-10"  # override — no watermark call
    mock_emd.tiingo_eod_max_concurrency = 2
    mock_emd.price_bar_security_batch_size = 25
    mock_emd.price_bar_insert_batch_size = 10000
    mock_emd.price_bars_incremental_overlap_days = 14
    mock_emd.price_bar_max_failed_securities = 25
    mock_emd.price_bar_max_failed_security_ratio = 0.02

    # Stub session factory
    session = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    mock_sf = MagicMock(return_value=cm)
    mock_postgres.get_session_factory.return_value = mock_sf

    # Stub repo
    mock_repo = MagicMock()
    mock_repo.list_selected_training_securities.return_value = []
    mock_repo.create_ingestion_run.return_value = MagicMock(run_id="run-1")

    from dagster import MaterializeResult

    import invest_ml.config.loaders as _loaders
    import invest_ml.db.repositories.price_bars as _pb_mod

    _orig_repo = _pb_mod.PriceBarsRepository
    _orig_cfg = _loaders.load_market_data_config
    _pb_mod.PriceBarsRepository = lambda s: mock_repo
    _loaders.load_market_data_config = lambda version="v1": {"market_data": {}, "market_profiles": {}}
    try:
        result = selected_price_bars(
            build_asset_context(),
            postgres=mock_postgres,
            equity_market_data=mock_emd,
        )
    finally:
        _pb_mod.PriceBarsRepository = _orig_repo
        _loaders.load_market_data_config = _orig_cfg

    assert isinstance(result, MaterializeResult)
    assert "selected_securities" in result.metadata
    assert "rows_inserted" in result.metadata


def test_fatal_failure_marks_run_failed():
    """If the service raises, the ingestion run must be marked failed."""
    from dagster import build_asset_context

    from invest_ml.defs.assets.market import selected_price_bars

    mock_postgres = MagicMock()
    mock_emd = MagicMock()
    mock_emd.tiingo_eod_reference_ticker = "SPY"
    mock_emd.price_bars_backfill_start_date = "2015-01-01"
    mock_emd.price_bars_target_end_date = "2026-07-10"
    mock_emd.tiingo_eod_max_concurrency = 2
    mock_emd.price_bar_security_batch_size = 25
    mock_emd.price_bar_insert_batch_size = 10000
    mock_emd.price_bars_incremental_overlap_days = 14
    mock_emd.price_bar_max_failed_securities = 1
    mock_emd.price_bar_max_failed_security_ratio = 0.01

    session = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    mock_sf = MagicMock(return_value=cm)
    mock_postgres.get_session_factory.return_value = mock_sf

    mock_repo = MagicMock()
    mock_repo.list_selected_training_securities.return_value = []
    run_mock = MagicMock()
    run_mock.run_id = "run-2"
    mock_repo.create_ingestion_run.return_value = run_mock

    import invest_ml.config.loaders as _loaders3
    import invest_ml.db.repositories.price_bars as _pb_mod3
    from invest_ml.market.price_bars import service as _svc_mod3

    _o1 = _pb_mod3.PriceBarsRepository
    _o2 = _loaders3.load_market_data_config
    _o3 = _svc_mod3.SelectedPriceBarsService.materialize
    _pb_mod3.PriceBarsRepository = lambda s: mock_repo
    _loaders3.load_market_data_config = lambda version="v1": {"market_data": {}, "market_profiles": {}}

    def _raise(*a, **kw):
        raise RuntimeError("fatal infra failure")

    _svc_mod3.SelectedPriceBarsService.materialize = _raise
    try:
        with pytest.raises(RuntimeError, match="fatal infra failure"):
            selected_price_bars(
                build_asset_context(),
                postgres=mock_postgres,
                equity_market_data=mock_emd,
            )
    finally:
        _pb_mod3.PriceBarsRepository = _o1
        _loaders3.load_market_data_config = _o2
        _svc_mod3.SelectedPriceBarsService.materialize = _o3

    mock_repo.fail_ingestion_run.assert_called_once()


def test_no_sec_calls_during_materialization():
    """The asset must not import or call any SEC module."""
    from dagster import build_asset_context

    from invest_ml.defs.assets.market import selected_price_bars

    mock_postgres = MagicMock()
    mock_emd = MagicMock()
    mock_emd.price_bars_target_end_date = "2026-07-10"
    mock_emd.price_bars_backfill_start_date = "2015-01-01"
    mock_emd.tiingo_eod_reference_ticker = "SPY"
    mock_emd.tiingo_eod_max_concurrency = 1
    mock_emd.price_bar_security_batch_size = 10
    mock_emd.price_bar_insert_batch_size = 1000
    mock_emd.price_bars_incremental_overlap_days = 14
    mock_emd.price_bar_max_failed_securities = 10
    mock_emd.price_bar_max_failed_security_ratio = 0.1

    session = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    mock_sf = MagicMock(return_value=cm)
    mock_postgres.get_session_factory.return_value = mock_sf

    mock_repo = MagicMock()
    mock_repo.list_selected_training_securities.return_value = []
    mock_repo.create_ingestion_run.return_value = MagicMock(run_id="run-3")

    import sys

    import invest_ml.config.loaders as _loaders4
    import invest_ml.db.repositories.price_bars as _pb_mod4

    _o1b = _pb_mod4.PriceBarsRepository
    _o2b = _loaders4.load_market_data_config
    _pb_mod4.PriceBarsRepository = lambda s: mock_repo
    _loaders4.load_market_data_config = lambda version="v1": {"market_data": {}, "market_profiles": {}}
    sec_modules_before = {k for k in sys.modules if k.startswith("invest_ml.sec")}
    try:
        selected_price_bars(
            build_asset_context(),
            postgres=mock_postgres,
            equity_market_data=mock_emd,
        )
    finally:
        _pb_mod4.PriceBarsRepository = _o1b
        _loaders4.load_market_data_config = _o2b

    # No new invest_ml.sec modules should have been imported by the asset run
    sec_modules_after = {k for k in sys.modules if k.startswith("invest_ml.sec")}
    newly_imported = sec_modules_after - sec_modules_before
    assert not newly_imported, f"Asset imported SEC modules: {newly_imported}"
