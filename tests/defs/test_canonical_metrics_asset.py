"""Smoke tests for the canonical_metrics Dagster asset definition."""



def test_canonical_metrics_asset_exists():
    from invest_ml.defs.assets.financials import canonical_metrics
    assert canonical_metrics is not None


def test_canonical_metrics_group_name():
    from invest_ml.defs.assets.financials import canonical_metrics
    group_names = set(canonical_metrics.group_names_by_key.values())
    assert "financial_warehouse" in group_names


def test_canonical_metrics_deps_include_xbrl_facts():
    from dagster import AssetKey

    from invest_ml.defs.assets.financials import canonical_metrics
    raw_deps = canonical_metrics.asset_deps[canonical_metrics.key]
    dep_keys = {d if isinstance(d, AssetKey) else d.asset_key for d in raw_deps}
    assert AssetKey("xbrl_facts") in dep_keys


def test_canonical_metrics_not_in_definitions_as_stub():
    """The asset must not raise NotImplementedError at import time."""
    from invest_ml.defs.assets.financials import canonical_metrics
    # Just importing and accessing the asset should not raise
    assert callable(canonical_metrics)


def test_definitions_includes_canonical_metrics():
    from dagster import AssetKey

    from invest_ml.definitions import defs
    asset_graph = defs.resolve_asset_graph()
    asset_keys = asset_graph.get_all_asset_keys()
    assert AssetKey("canonical_metrics") in asset_keys
