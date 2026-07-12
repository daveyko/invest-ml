"""Smoke tests for the training_universe Dagster asset definition."""


def test_training_universe_asset_exists():
    from invest_ml.defs.assets.discovery import training_universe

    assert training_universe is not None
    assert training_universe.key.path == ["training_universe"]


def test_training_universe_is_in_discovery_group():
    from invest_ml.defs.assets.discovery import training_universe

    assert training_universe.group_names_by_key[training_universe.key] == "discovery"


def test_training_universe_depends_on_company_market_profiles():
    from dagster import AssetKey

    from invest_ml.defs.assets.discovery import training_universe

    raw_deps = training_universe.asset_deps[training_universe.key]
    # asset_deps values are AssetKey objects in this Dagster version
    dep_keys = {d if isinstance(d, AssetKey) else d.asset_key for d in raw_deps}
    assert AssetKey("company_market_profiles") in dep_keys


def test_training_universe_not_scoring_dependency():
    """scoring_universe must depend on training_universe, not candidate_universe."""
    from dagster import AssetKey

    from invest_ml.defs.assets.discovery import scoring_universe

    raw_deps = scoring_universe.asset_deps[scoring_universe.key]
    dep_keys = {d if isinstance(d, AssetKey) else d.asset_key for d in raw_deps}
    assert AssetKey("training_universe") in dep_keys
    assert AssetKey("candidate_universe") not in dep_keys
