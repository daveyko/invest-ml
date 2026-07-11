"""Validate YAML configuration files and cross-file consistency."""

import pytest


def test_sic_buckets_loads() -> None:
    from invest_ml.config.loaders import load_sic_buckets

    cfg = load_sic_buckets()
    assert "model_buckets" in cfg
    buckets = cfg["model_buckets"]
    assert "semiconductors" in buckets
    assert "software_and_data" in buckets
    for bucket_name, bucket in buckets.items():
        assert "sic_codes" in bucket, f"{bucket_name} missing sic_codes"
        assert len(bucket["sic_codes"]) > 0


def test_universe_config_loads() -> None:
    from invest_ml.config.loaders import load_universe_config

    cfg = load_universe_config()
    assert "candidate" in cfg
    assert "training" in cfg
    assert "scoring" in cfg

    candidate = cfg["candidate"]
    assert "exchanges" in candidate
    assert len(candidate["exchanges"]) > 0

    training = cfg["training"]
    assert training["minimum_annual_periods"] > 0
    assert training["minimum_price_history_years"] > 0
    assert 0 < training["minimum_canonical_metric_coverage"] <= 1.0

    scoring = cfg["scoring"]
    assert "model_buckets" in scoring
    assert "always_include" in scoring
    assert len(scoring["always_include"]) > 0


def test_canonical_metrics_loads() -> None:
    from invest_ml.config.loaders import load_canonical_metrics

    cfg = load_canonical_metrics()
    assert "metrics" in cfg
    metrics = cfg["metrics"]

    required = {
        "revenue", "operating_income", "net_income", "operating_cash_flow",
        "capex", "cash", "total_assets", "total_liabilities",
        "stockholders_equity", "long_term_debt", "diluted_shares",
    }
    for name in required:
        assert name in metrics, f"Metric '{name}' missing from canonical_metrics config"
        m = metrics[name]
        assert "tags" in m and len(m["tags"]) > 0
        assert "unit" in m
        assert "duration" in m
        assert "allows_ttm" in m


def test_features_config_loads() -> None:
    from invest_ml.config.loaders import load_features_config

    cfg = load_features_config()
    assert "features" in cfg
    assert "feature_set" in cfg

    features = cfg["features"]
    for name, defn in features.items():
        assert "version" in defn, f"Feature '{name}' missing version"
        assert "description" in defn
        assert "inputs" in defn
        assert "point_in_time_rule" in defn


def test_feature_set_members_reference_declared_features() -> None:
    """Every member in feature_set.members must reference a declared feature with matching version."""
    from invest_ml.config.loaders import load_features_config

    cfg = load_features_config()
    features = cfg["features"]
    members = cfg["feature_set"]["members"]

    for member in members:
        name = member["name"]
        version = member["version"]
        assert name in features, f"Feature set member '{name}' not in features"
        assert features[name]["version"] == version, (
            f"Feature set member '{name}' version '{version}' "
            f"!= declared '{features[name]['version']}'"
        )


def test_target_spec_loads() -> None:
    from invest_ml.config.loaders import load_target_spec

    cfg = load_target_spec()
    assert cfg["name"] == "return_threshold"
    assert cfg["version"] == "v1"
    assert cfg["horizon_months"] == 12
    assert cfg["return_threshold"] == 0.15
    assert "definition" in cfg
    assert "label" in cfg["definition"]


def test_scoring_universe_model_buckets_reference_sic_config() -> None:
    """All model_buckets listed in universe scoring must be declared in sic_buckets."""
    from invest_ml.config.loaders import load_sic_buckets, load_universe_config

    sic = load_sic_buckets()
    universe = load_universe_config()
    declared = set(sic["model_buckets"].keys())
    required = set(universe["scoring"]["model_buckets"])
    missing = required - declared
    assert not missing, f"Scoring universe references undeclared buckets: {missing}"
