"""Configuration tests for the feature registry.

All tests operate purely on the YAML config and Python models — no DB, no APIs.
"""


import pytest

from invest_ml.config.loaders import load_feature_registry_config
from invest_ml.features.config import (
    compute_feature_hash,
    compute_feature_set_hash,
    parse_feature_registry_config,
)
from invest_ml.features.validator import RegistryValidationError, validate_registry

_KNOWN_METRICS = {
    "revenue", "gross_profit", "operating_income", "net_income",
    "operating_cash_flow", "capital_expenditures", "research_and_development_expense",
    "stock_based_compensation", "diluted_weighted_average_shares",
    "cash_and_cash_equivalents", "debt_current", "long_term_debt",
    "total_assets", "total_liabilities", "stockholders_equity", "shares_outstanding",
}


def _load_raw() -> dict:
    return load_feature_registry_config("compounder", "v1")


# ── Loading ────────────────────────────────────────────────────────────────────


def test_compounder_v1_loads():
    raw = _load_raw()
    assert raw["registry_version"] == "feature_registry_v1"
    assert "feature_sets" in raw
    assert "features" in raw
    assert "compounder_v1" in raw["feature_sets"]
    assert len(raw["features"]) == 32


def test_compounder_v1_feature_set_has_32_members():
    raw = _load_raw()
    members = raw["feature_sets"]["compounder_v1"]["features"]
    assert len(members) == 32


def test_all_feature_set_members_are_declared():
    raw = _load_raw()
    declared = set(raw["features"].keys())
    members = set(raw["feature_sets"]["compounder_v1"]["features"])
    missing = members - declared
    assert not missing, f"Members not in features: {missing}"


def test_compounder_v1_parses_into_typed_config():
    raw = _load_raw()
    cfg = parse_feature_registry_config(raw)
    assert cfg.registry_version == "feature_registry_v1"
    assert len(cfg.features) == 32
    assert len(cfg.feature_sets) == 1
    assert cfg.feature_sets[0].name == "compounder"
    assert cfg.feature_sets[0].version == "compounder_v1"


# ── Hash determinism ───────────────────────────────────────────────────────────


def test_registry_hash_is_deterministic():
    raw = _load_raw()
    cfg1 = parse_feature_registry_config(raw)
    cfg2 = parse_feature_registry_config(raw)
    assert cfg1.configuration_hash == cfg2.configuration_hash


def test_feature_hash_is_deterministic():
    h1 = compute_feature_hash(
        "gross_margin", "v1",
        {"operation": "ratio", "numerator": {"source": "canonical_metric"}},
        {"selection": "latest_available_on_or_before_as_of_date"},
        {"behavior": "null"},
    )
    h2 = compute_feature_hash(
        "gross_margin", "v1",
        {"operation": "ratio", "numerator": {"source": "canonical_metric"}},
        {"selection": "latest_available_on_or_before_as_of_date"},
        {"behavior": "null"},
    )
    assert h1 == h2


def test_reordered_yaml_dict_keys_same_hash():
    """Dict key order must not affect the configuration hash."""
    definition_a = {
        "operation": "ratio",
        "alignment": "same_period_end",
        "numerator": {"source": "canonical_metric", "metric_name": "gross_profit"},
    }
    definition_b = {
        "numerator": {"metric_name": "gross_profit", "source": "canonical_metric"},
        "alignment": "same_period_end",
        "operation": "ratio",
    }
    pit = {"selection": "latest_available_on_or_before_as_of_date"}
    mvp = {"behavior": "null"}
    h_a = compute_feature_hash("gross_margin", "v1", definition_a, pit, mvp)
    h_b = compute_feature_hash("gross_margin", "v1", definition_b, pit, mvp)
    assert h_a == h_b


def test_feature_order_affects_feature_set_hash():
    """Different member ordering must produce a different feature-set hash."""
    members_v1 = [("gross_margin", "v1", "abc"), ("net_margin", "v1", "def")]
    members_v2 = [("net_margin", "v1", "def"), ("gross_margin", "v1", "abc")]
    h1 = compute_feature_set_hash("compounder", "compounder_v1", "company_security", "monthly", members_v1)
    h2 = compute_feature_set_hash("compounder", "compounder_v1", "company_security", "monthly", members_v2)
    assert h1 != h2


def test_different_feature_versions_produce_different_hashes():
    pit = {"selection": "latest_available_on_or_before_as_of_date"}
    mvp = {"behavior": "null"}
    defn = {"operation": "ratio"}
    h1 = compute_feature_hash("gross_margin", "v1", defn, pit, mvp)
    h2 = compute_feature_hash("gross_margin", "v2", defn, pit, mvp)
    assert h1 != h2


# ── Validation: structural errors ─────────────────────────────────────────────


def test_missing_registry_version_fails():
    raw = _load_raw()
    del raw["registry_version"]
    with pytest.raises((RegistryValidationError, ValueError), match="registry_version"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_missing_feature_version_fails():
    raw = _load_raw()
    raw["features"]["gross_margin"] = {
        "category": "fundamental_quality",
        "entity_grain": "company_security",
        "value_type": "float",
        "description": "test",
        "definition": {"operation": "ratio"},
        "point_in_time_policy": {},
        "missing_value_policy": {"behavior": "null"},
        # no "version"
    }
    with pytest.raises(RegistryValidationError, match="version"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_duplicate_feature_set_members_fail():
    raw = _load_raw()
    raw["feature_sets"]["compounder_v1"]["features"] = [
        "gross_margin",
        "gross_margin",  # duplicate
        "net_margin",
    ]
    with pytest.raises(RegistryValidationError, match="duplicate"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_feature_set_member_not_in_features_fails():
    raw = _load_raw()
    raw["feature_sets"]["compounder_v1"]["features"] = list(
        raw["feature_sets"]["compounder_v1"]["features"]
    ) + ["nonexistent_feature_xyz"]
    with pytest.raises(RegistryValidationError, match="nonexistent_feature_xyz"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_missing_feature_set_version_fails():
    raw = _load_raw()
    raw["feature_sets"]["compounder_v1"]["version"] = ""
    with pytest.raises(RegistryValidationError, match="version"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


# ── Validation: operation and source errors ────────────────────────────────────


def test_unsupported_operation_fails():
    raw = _load_raw()
    raw["features"]["gross_margin"]["definition"] = {
        "operation": "arbitrary_sql_injection",
        "numerator": {"source": "canonical_metric", "metric_name": "gross_profit", "period_type": "annual"},
        "denominator": {"source": "canonical_metric", "metric_name": "revenue", "period_type": "annual"},
    }
    with pytest.raises(RegistryValidationError, match="unsupported operation"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_unknown_source_type_fails():
    raw = _load_raw()
    raw["features"]["gross_margin"]["definition"] = {
        "operation": "ratio",
        "numerator": {
            "source": "arbitrary_python_import",  # rejected
            "metric_name": "gross_profit",
        },
    }
    with pytest.raises(RegistryValidationError, match="unsupported source type"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_invalid_lookback_zero_fails():
    raw = _load_raw()
    raw["features"]["adjusted_return_21d"]["definition"]["lookback_observations"] = 0
    with pytest.raises(RegistryValidationError, match="lookback_observations"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_invalid_lookback_negative_fails():
    raw = _load_raw()
    raw["features"]["adjusted_return_21d"]["definition"]["lookback_observations"] = -5
    with pytest.raises(RegistryValidationError, match="lookback_observations"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


# ── Validation: source references ────────────────────────────────────────────


def test_unknown_canonical_metric_fails():
    raw = _load_raw()
    raw["features"]["gross_margin"]["definition"] = {
        "operation": "ratio",
        "numerator": {
            "source": "canonical_metric",
            "metric_name": "nonexistent_metric_xyz",
            "period_type": "annual",
        },
        "denominator": {
            "source": "canonical_metric",
            "metric_name": "revenue",
            "period_type": "annual",
        },
        "alignment": "same_period_end",
    }
    with pytest.raises(RegistryValidationError, match="nonexistent_metric_xyz"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_tiingo_field_names_rejected():
    raw = _load_raw()
    raw["features"]["adjusted_return_21d"]["definition"] = {
        "operation": "adjusted_return",
        "input": {
            "source": "price_bar",
            "field": "adjClose",  # Tiingo JSON field name — must be rejected
        },
        "lookback_observations": 21,
        "minimum_observations": 22,
    }
    with pytest.raises(RegistryValidationError, match="raw provider field"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_unknown_price_bar_field_fails():
    raw = _load_raw()
    raw["features"]["adjusted_return_21d"]["definition"] = {
        "operation": "adjusted_return",
        "input": {
            "source": "price_bar",
            "field": "nonexistent_column_xyz",
        },
        "lookback_observations": 21,
        "minimum_observations": 22,
    }
    with pytest.raises(RegistryValidationError, match="unknown price_bar field"):
        validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)


def test_valid_registry_passes_validation():
    raw = _load_raw()
    validate_registry(raw, known_canonical_metrics=_KNOWN_METRICS)  # must not raise
