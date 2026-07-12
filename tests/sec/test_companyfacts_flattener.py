"""Tests for CompanyFactsFlattener and supporting functions."""

import json
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from invest_ml.sec.companyfacts_flattener import (
    CompanyFactsFlattener,
    _fact_id,
    _registry_hash,
    build_fact_registry,
)

SAMPLE_METRICS_CONFIG = {
    "metrics": {
        "revenue": {
            "period_kind": "duration",
            "expected_units": ["USD"],
            "concepts": [
                {"taxonomy": "us-gaap", "tag": "Revenues", "priority": 2},
                {
                    "taxonomy": "us-gaap",
                    "tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "priority": 1,
                },
            ],
        },
        "net_income": {
            "period_kind": "duration",
            "expected_units": ["USD"],
            "concepts": [
                {"taxonomy": "us-gaap", "tag": "NetIncomeLoss", "priority": 1},
            ],
        },
        "diluted_shares": {
            "period_kind": "duration",
            "expected_units": ["shares"],
            "concepts": [
                {
                    "taxonomy": "us-gaap",
                    "tag": "WeightedAverageNumberOfDilutedSharesOutstanding",
                    "priority": 1,
                },
            ],
        },
    }
}


def _make_payload(tag: str, observations: list, unit: str = "USD") -> bytes:
    data = {
        "cik": 1234567,
        "facts": {
            "us-gaap": {
                tag: {
                    "label": f"Label for {tag}",
                    "description": "Test description",
                    "units": {unit: observations},
                }
            }
        },
    }
    return json.dumps(data).encode()


def test_build_fact_registry_includes_all_tags():
    registry = build_fact_registry(SAMPLE_METRICS_CONFIG)
    assert ("us-gaap", "Revenues") in registry
    assert ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax") in registry
    assert ("us-gaap", "NetIncomeLoss") in registry
    assert ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding") in registry
    assert registry[("us-gaap", "Revenues")] == "revenue"
    assert registry[("us-gaap", "NetIncomeLoss")] == "net_income"


def test_build_fact_registry_first_metric_wins_on_duplicate_tag():
    config = {
        "metrics": {
            "first": {
                "concepts": [{"taxonomy": "us-gaap", "tag": "SharedTag", "priority": 1}]
            },
            "second": {
                "concepts": [{"taxonomy": "us-gaap", "tag": "SharedTag", "priority": 1}]
            },
        }
    }
    registry = build_fact_registry(config)
    assert registry[("us-gaap", "SharedTag")] == "first"


def test_registry_hash_is_deterministic():
    r1 = build_fact_registry(SAMPLE_METRICS_CONFIG)
    r2 = build_fact_registry(SAMPLE_METRICS_CONFIG)
    assert _registry_hash(r1) == _registry_hash(r2)


def test_derivation_version_contains_registry_hash():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    assert flattener.derivation_version.startswith("companyfacts_flattener_v1:")
    assert len(flattener.derivation_version) > len("companyfacts_flattener_v1:")


def test_derivation_version_changes_with_registry():
    f1 = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    config2 = {
        "metrics": {
            "revenue": {
                "concepts": [{"taxonomy": "us-gaap", "tag": "OnlyRevenues", "priority": 1}]
            },
        }
    }
    f2 = CompanyFactsFlattener.from_config(config2)
    assert f1.derivation_version != f2.derivation_version


def test_flatten_basic_observation():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    company_id = uuid4()
    raw_version_id = uuid4()

    obs = {
        "start": "2023-01-01",
        "end": "2023-12-31",
        "val": 1000000,
        "accn": "0001234567-24-000001",
        "fy": 2023,
        "fp": "FY",
        "form": "10-K",
        "filed": "2024-02-15",
        "frame": "CY2023",
    }
    payload = _make_payload("Revenues", [obs])
    facts = flattener.flatten(company_id, raw_version_id, payload)

    assert len(facts) == 1
    f = facts[0]
    assert f.company_id == company_id
    assert f.raw_version_id == raw_version_id
    assert f.taxonomy == "us-gaap"
    assert f.tag == "Revenues"
    assert f.unit == "USD"
    assert f.period_start == date(2023, 1, 1)
    assert f.period_end == date(2023, 12, 31)
    assert f.value == Decimal("1000000")
    assert f.fiscal_year == 2023
    assert f.fiscal_period == "FY"
    assert f.form == "10-K"
    assert f.filed_date == date(2024, 2, 15)
    assert f.frame == "CY2023"
    assert f.accession_number == "0001234567-24-000001"
    assert f.dimensions == {}


def test_flatten_filters_unregistered_tags():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    company_id = uuid4()
    raw_version_id = uuid4()

    obs = {"end": "2023-12-31", "val": 999, "filed": "2024-02-01"}
    payload = _make_payload("UnknownTag", [obs])
    facts = flattener.flatten(company_id, raw_version_id, payload)
    assert facts == []


def test_flatten_drops_observation_missing_required_fields():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    company_id = uuid4()
    raw_version_id = uuid4()

    missing_end = {"val": 100, "filed": "2024-01-01"}
    missing_filed = {"end": "2023-12-31", "val": 100}
    missing_val = {"end": "2023-12-31", "filed": "2024-01-01"}

    for bad_obs in [missing_end, missing_filed, missing_val]:
        payload = _make_payload("Revenues", [bad_obs])
        facts = flattener.flatten(company_id, raw_version_id, payload)
        assert facts == [], f"Expected no facts for {bad_obs}"


def test_flatten_drops_observation_with_start_after_end():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    obs = {
        "start": "2024-01-01",
        "end": "2023-12-31",
        "val": 100,
        "filed": "2024-02-01",
    }
    payload = _make_payload("Revenues", [obs])
    facts = flattener.flatten(uuid4(), uuid4(), payload)
    assert facts == []


def test_flatten_instant_observation_no_start():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    obs = {"end": "2023-12-31", "val": 500, "filed": "2024-02-01"}
    payload = _make_payload("NetIncomeLoss", [obs])
    facts = flattener.flatten(uuid4(), uuid4(), payload)
    assert len(facts) == 1
    assert facts[0].period_start is None


def test_flatten_decimal_safe_large_value():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    obs = {"end": "2023-12-31", "val": 123456789012345, "filed": "2024-02-01"}
    payload = _make_payload("Revenues", [obs])
    facts = flattener.flatten(uuid4(), uuid4(), payload)
    assert len(facts) == 1
    assert facts[0].value == Decimal("123456789012345")


def test_flatten_multiple_observations():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    observations = [
        {"end": "2021-12-31", "val": 100, "filed": "2022-02-01"},
        {"end": "2022-12-31", "val": 200, "filed": "2023-02-01"},
        {"end": "2023-12-31", "val": 300, "filed": "2024-02-01"},
    ]
    payload = _make_payload("Revenues", observations)
    facts = flattener.flatten(uuid4(), uuid4(), payload)
    assert len(facts) == 3
    values = sorted(f.value for f in facts)
    assert values == [Decimal("100"), Decimal("200"), Decimal("300")]


def test_flatten_dimensions_captured():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    obs = {
        "end": "2023-12-31",
        "val": 100,
        "filed": "2024-02-01",
        "segment": "North America",
    }
    payload = _make_payload("Revenues", [obs])
    facts = flattener.flatten(uuid4(), uuid4(), payload)
    assert len(facts) == 1
    assert facts[0].dimensions == {"segment": "North America"}


def test_fact_id_is_deterministic():
    company_id = UUID("12345678-1234-5678-1234-567812345678")
    kwargs = dict(
        company_id=company_id,
        taxonomy="us-gaap",
        tag="Revenues",
        unit="USD",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
        value=Decimal("1000000"),
        accession_number="0001234567-24-000001",
        fiscal_year=2023,
        fiscal_period="FY",
        form="10-K",
        filed_date=date(2024, 2, 15),
        frame="CY2023",
        dimensions={},
    )
    id1 = _fact_id(**kwargs)
    id2 = _fact_id(**kwargs)
    assert id1 == id2


def test_fact_id_ignores_raw_version_id():
    company_id = UUID("12345678-1234-5678-1234-567812345678")
    kwargs = dict(
        company_id=company_id,
        taxonomy="us-gaap",
        tag="Revenues",
        unit="USD",
        period_start=None,
        period_end=date(2023, 12, 31),
        value=Decimal("100"),
        accession_number=None,
        fiscal_year=None,
        fiscal_period=None,
        form=None,
        filed_date=date(2024, 2, 1),
        frame=None,
        dimensions={},
    )
    id1 = _fact_id(**kwargs)
    id2 = _fact_id(**kwargs)
    assert id1 == id2


def test_fact_id_differs_on_value_change():
    company_id = UUID("12345678-1234-5678-1234-567812345678")
    base_kwargs = dict(
        company_id=company_id,
        taxonomy="us-gaap",
        tag="Revenues",
        unit="USD",
        period_start=None,
        period_end=date(2023, 12, 31),
        value=Decimal("100"),
        accession_number=None,
        fiscal_year=None,
        fiscal_period=None,
        form=None,
        filed_date=date(2024, 2, 1),
        frame=None,
        dimensions={},
    )
    id1 = _fact_id(**base_kwargs)
    id2 = _fact_id(**{**base_kwargs, "value": Decimal("200")})
    assert id1 != id2


def test_flatten_invalid_json_raises():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    with pytest.raises(ValueError, match="JSON parse failed"):
        flattener.flatten(uuid4(), uuid4(), b"not valid json{")


def test_flatten_empty_facts_section():
    flattener = CompanyFactsFlattener.from_config(SAMPLE_METRICS_CONFIG)
    payload = json.dumps({"cik": 1, "facts": {}}).encode()
    facts = flattener.flatten(uuid4(), uuid4(), payload)
    assert facts == []
