"""Tests for CanonicalMetricRegistry."""

import pytest

from invest_ml.canonical.registry import CanonicalMetricRegistry

_MINIMAL_CFG = {
    "version": "canonical_metrics_v1",
    "defaults": {
        "annual_forms": ["10-K", "10-K/A", "20-F"],
        "quarterly_forms": ["10-Q", "10-Q/A"],
        "annual_duration_days": {"min": 300, "max": 430},
        "quarterly_duration_days": {"min": 60, "max": 120},
    },
    "metrics": {
        "revenue": {
            "period_kind": "duration",
            "expected_units": ["USD"],
            "concepts": [
                {"taxonomy": "us-gaap", "tag": "Revenues", "priority": 2},
                {"taxonomy": "us-gaap", "tag": "RevenueFromContractWithCustomerExcludingAssessedTax", "priority": 1},
            ],
        },
        "total_assets": {
            "period_kind": "instant",
            "expected_units": ["USD"],
            "concepts": [
                {"taxonomy": "us-gaap", "tag": "Assets", "priority": 1},
            ],
        },
    },
}


def test_registry_loads_metrics():
    registry = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    assert "revenue" in registry.metrics
    assert "total_assets" in registry.metrics
    assert len(registry.metrics) == 2


def test_registry_concept_sorted_by_priority():
    registry = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    concepts = registry.metrics["revenue"].concepts
    # priority 1 should come first
    assert concepts[0].priority == 1
    assert concepts[0].tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert concepts[1].priority == 2
    assert concepts[1].tag == "Revenues"


def test_registry_taxonomy_tags():
    registry = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    tags = registry.taxonomy_tags()
    assert ("us-gaap", "Revenues") in tags
    assert ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax") in tags
    assert ("us-gaap", "Assets") in tags
    assert len(tags) == 3


def test_registry_find_concept_returns_metric_and_config():
    registry = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    result = registry.find_concept("us-gaap", "Revenues")
    assert result is not None
    metric_name, concept = result
    assert metric_name == "revenue"
    assert concept.tag == "Revenues"
    assert concept.priority == 2


def test_registry_find_concept_returns_none_for_unknown():
    registry = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    assert registry.find_concept("us-gaap", "UnknownTag") is None
    assert registry.find_concept("dei", "Assets") is None


def test_registry_configuration_hash_is_deterministic():
    r1 = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    r2 = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    assert r1.configuration_hash == r2.configuration_hash


def test_registry_configuration_hash_changes_with_metrics():
    cfg_alt = {
        **_MINIMAL_CFG,
        "metrics": {
            "revenue": {
                "period_kind": "duration",
                "expected_units": ["USD"],
                "concepts": [
                    {"taxonomy": "us-gaap", "tag": "OnlyRevenues", "priority": 1},
                ],
            },
        },
    }
    r1 = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    r2 = CanonicalMetricRegistry.from_config(cfg_alt)
    assert r1.configuration_hash != r2.configuration_hash


def test_registry_hash_is_256_hex():
    registry = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    assert len(registry.configuration_hash) == 64
    int(registry.configuration_hash, 16)  # must be valid hex


def test_registry_forms_loaded():
    registry = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    assert "10-K" in registry.annual_forms
    assert "20-F" in registry.annual_forms
    assert "10-Q" in registry.quarterly_forms


def test_registry_duration_bounds_loaded():
    registry = CanonicalMetricRegistry.from_config(_MINIMAL_CFG)
    assert registry.annual_duration_min == 300
    assert registry.annual_duration_max == 430
    assert registry.quarterly_duration_min == 60
    assert registry.quarterly_duration_max == 120


def test_registry_rejects_invalid_period_kind():
    bad_cfg = {
        **_MINIMAL_CFG,
        "metrics": {
            "revenue": {
                "period_kind": "flow",  # invalid
                "expected_units": ["USD"],
                "concepts": [{"taxonomy": "us-gaap", "tag": "Revenues", "priority": 1}],
            }
        },
    }
    with pytest.raises(ValueError, match="invalid period_kind"):
        CanonicalMetricRegistry.from_config(bad_cfg)


def test_registry_rejects_empty_concepts():
    bad_cfg = {
        **_MINIMAL_CFG,
        "metrics": {
            "revenue": {
                "period_kind": "duration",
                "expected_units": ["USD"],
                "concepts": [],
            }
        },
    }
    with pytest.raises(ValueError, match="concepts list is empty"):
        CanonicalMetricRegistry.from_config(bad_cfg)


def test_registry_rejects_empty_expected_units():
    bad_cfg = {
        **_MINIMAL_CFG,
        "metrics": {
            "revenue": {
                "period_kind": "duration",
                "expected_units": [],
                "concepts": [{"taxonomy": "us-gaap", "tag": "Revenues", "priority": 1}],
            }
        },
    }
    with pytest.raises(ValueError, match="expected_units is empty"):
        CanonicalMetricRegistry.from_config(bad_cfg)


def test_registry_rejects_duplicate_priorities():
    bad_cfg = {
        **_MINIMAL_CFG,
        "metrics": {
            "revenue": {
                "period_kind": "duration",
                "expected_units": ["USD"],
                "concepts": [
                    {"taxonomy": "us-gaap", "tag": "Revenues", "priority": 1},
                    {"taxonomy": "us-gaap", "tag": "AltRevenues", "priority": 1},
                ],
            }
        },
    }
    with pytest.raises(ValueError, match="duplicate concept priorities"):
        CanonicalMetricRegistry.from_config(bad_cfg)


def test_registry_first_concept_wins_cross_metric_same_tag():
    """When the same (taxonomy, tag) appears in two metrics, first metric wins."""
    cfg = {
        **_MINIMAL_CFG,
        "metrics": {
            "first_metric": {
                "period_kind": "duration",
                "expected_units": ["USD"],
                "concepts": [{"taxonomy": "us-gaap", "tag": "SharedTag", "priority": 1}],
            },
            "second_metric": {
                "period_kind": "duration",
                "expected_units": ["USD"],
                "concepts": [{"taxonomy": "us-gaap", "tag": "SharedTag", "priority": 1}],
            },
        },
    }
    registry = CanonicalMetricRegistry.from_config(cfg)
    result = registry.find_concept("us-gaap", "SharedTag")
    assert result is not None
    assert result[0] == "first_metric"


def test_registry_full_yaml_loads_16_metrics():
    """Confirm the actual canonical_metrics_v1.yaml contains exactly 16 canonical metrics."""
    from invest_ml.config.loaders import load_canonical_metrics
    cfg = load_canonical_metrics()
    registry = CanonicalMetricRegistry.from_config(cfg)
    expected = {
        "revenue", "gross_profit", "operating_income", "net_income",
        "operating_cash_flow", "capital_expenditures", "research_and_development_expense",
        "stock_based_compensation", "diluted_weighted_average_shares",
        "cash_and_cash_equivalents", "debt_current", "long_term_debt",
        "total_assets", "total_liabilities", "stockholders_equity", "shares_outstanding",
    }
    assert expected == set(registry.metrics.keys())
