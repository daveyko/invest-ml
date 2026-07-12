"""Smoke tests for the xbrl_facts Dagster asset definition."""


def test_xbrl_facts_asset_exists():
    from invest_ml.defs.assets.financials import xbrl_facts

    assert xbrl_facts is not None
    assert xbrl_facts.key.path == ["xbrl_facts"]


def test_xbrl_facts_is_in_financial_warehouse_group():
    from invest_ml.defs.assets.financials import xbrl_facts

    assert xbrl_facts.group_names_by_key[xbrl_facts.key] == "financial_warehouse"


def test_xbrl_facts_depends_on_training_universe():
    from dagster import AssetKey

    from invest_ml.defs.assets.financials import xbrl_facts

    raw_deps = xbrl_facts.asset_deps[xbrl_facts.key]
    dep_keys = {d if isinstance(d, AssetKey) else d.asset_key for d in raw_deps}
    assert AssetKey("training_universe") in dep_keys


def test_xbrl_facts_does_not_depend_on_selected_companyfacts_raw():
    from dagster import AssetKey

    from invest_ml.defs.assets.financials import xbrl_facts

    raw_deps = xbrl_facts.asset_deps[xbrl_facts.key]
    dep_keys = {d if isinstance(d, AssetKey) else d.asset_key for d in raw_deps}
    assert AssetKey("selected_companyfacts_raw") not in dep_keys


def test_canonical_metrics_depends_on_xbrl_facts():
    from dagster import AssetKey

    from invest_ml.defs.assets.financials import canonical_metrics

    raw_deps = canonical_metrics.asset_deps[canonical_metrics.key]
    dep_keys = {d if isinstance(d, AssetKey) else d.asset_key for d in raw_deps}
    assert AssetKey("xbrl_facts") in dep_keys


def test_selected_companyfacts_raw_not_importable():
    """selected_companyfacts_raw has been removed from the codebase."""
    import inspect

    import invest_ml.defs.assets.financials as m

    names = [name for name, _ in inspect.getmembers(m) if not name.startswith("_")]
    assert "selected_companyfacts_raw" not in names
