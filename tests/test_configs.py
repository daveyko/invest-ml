"""Validate YAML configuration files and cross-file consistency."""



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
    assert "supported_exchanges" in candidate
    assert len(candidate["supported_exchanges"]) > 0
    assert "exchange_aliases" in candidate

    training = cfg["training"]
    assert training["minimum_annual_periods"] > 0
    assert training["minimum_price_history_years"] > 0
    assert 0 < training["minimum_canonical_metric_coverage"] <= 1.0

    scoring = cfg["scoring"]
    assert "included_model_buckets" in scoring
    assert len(scoring["included_model_buckets"]) > 0
    assert "manual_include_tickers" in scoring
    assert "manual_exclude_ciks" in scoring


def test_canonical_metrics_loads() -> None:
    from invest_ml.config.loaders import load_canonical_metrics

    cfg = load_canonical_metrics()
    assert "version" in cfg
    assert "defaults" in cfg
    assert "metrics" in cfg

    defaults = cfg["defaults"]
    assert "annual_forms" in defaults and len(defaults["annual_forms"]) > 0
    assert "quarterly_forms" in defaults and len(defaults["quarterly_forms"]) > 0
    assert "annual_duration_days" in defaults
    assert "quarterly_duration_days" in defaults

    metrics = cfg["metrics"]
    required = {
        "revenue", "gross_profit", "operating_income", "net_income",
        "operating_cash_flow", "capital_expenditures", "research_and_development_expense",
        "stock_based_compensation", "diluted_weighted_average_shares",
        "cash_and_cash_equivalents", "debt_current", "long_term_debt",
        "total_assets", "total_liabilities", "stockholders_equity", "shares_outstanding",
    }
    assert len(metrics) >= len(required), "Expected at least 16 canonical metrics"
    for name in required:
        assert name in metrics, f"Metric '{name}' missing from canonical_metrics config"
        m = metrics[name]
        assert "period_kind" in m, f"Metric '{name}' missing period_kind"
        assert m["period_kind"] in ("duration", "instant"), f"Metric '{name}' invalid period_kind"
        assert "expected_units" in m and len(m["expected_units"]) > 0, (
            f"Metric '{name}' missing expected_units"
        )
        assert "concepts" in m and len(m["concepts"]) > 0, (
            f"Metric '{name}' missing concepts"
        )
        for concept in m["concepts"]:
            assert "taxonomy" in concept and concept["taxonomy"], (
                f"Metric '{name}' concept missing taxonomy"
            )
            assert "tag" in concept and concept["tag"], (
                f"Metric '{name}' concept missing tag"
            )
            assert "priority" in concept, f"Metric '{name}' concept missing priority"


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
    required = set(universe["scoring"]["included_model_buckets"])
    missing = required - declared
    assert not missing, f"Scoring universe references undeclared buckets: {missing}"
