"""Smoke tests for the scoring_universe Dagster asset definition."""


def test_scoring_universe_asset_exists():
    from invest_ml.defs.assets.discovery import scoring_universe

    assert scoring_universe is not None
    assert scoring_universe.key.path == ["scoring_universe"]


def test_scoring_universe_is_in_discovery_group():
    from invest_ml.defs.assets.discovery import scoring_universe

    assert scoring_universe.group_names_by_key[scoring_universe.key] == "discovery"


def test_scoring_universe_depends_on_training_universe():
    from dagster import AssetKey

    from invest_ml.defs.assets.discovery import scoring_universe

    raw_deps = scoring_universe.asset_deps[scoring_universe.key]
    dep_keys = {d if isinstance(d, AssetKey) else d.asset_key for d in raw_deps}
    assert AssetKey("training_universe") in dep_keys
    assert AssetKey("candidate_universe") not in dep_keys


def test_scoring_universe_has_description():
    """Verify the asset has a meaningful description (stub had no real description)."""
    from invest_ml.defs.assets.discovery import scoring_universe

    desc = scoring_universe.descriptions_by_key.get(scoring_universe.key, "")
    assert desc, "scoring_universe should have a description"
    assert "scoring" in desc.lower() or "bucket" in desc.lower() or "sic" in desc.lower()
