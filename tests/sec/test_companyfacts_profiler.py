"""Unit tests for CompanyFactsProfiler.

Uses synthetic ProfilingConfig and CompanyFacts JSON payloads — no network,
no database, no real YAML loading (except one integration-style smoke test).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import orjson

from invest_ml.sec.profiler import (
    CompanyFactsProfiler,
    ConceptSpec,
    ProfilingConfig,
    ProfilingMetricSpec,
)

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
_COMPANY_ID = uuid4()
_RUN_ID = uuid4()
_VERSION = "test_v1"


# ── Minimal config for testing ───────────────────────────────────────────────


def _minimal_config(required=None) -> ProfilingConfig:
    """Config with all 7 required metrics using a single concept each."""
    metrics = {
        "revenue": ProfilingMetricSpec(
            name="revenue",
            period_kind="duration",
            units=frozenset(["USD"]),
            concepts=(ConceptSpec("us-gaap", "Revenues"),),
        ),
        "operating_income": ProfilingMetricSpec(
            name="operating_income",
            period_kind="duration",
            units=frozenset(["USD"]),
            concepts=(ConceptSpec("us-gaap", "OperatingIncomeLoss"),),
        ),
        "net_income": ProfilingMetricSpec(
            name="net_income",
            period_kind="duration",
            units=frozenset(["USD"]),
            concepts=(ConceptSpec("us-gaap", "NetIncomeLoss"),),
        ),
        "operating_cash_flow": ProfilingMetricSpec(
            name="operating_cash_flow",
            period_kind="duration",
            units=frozenset(["USD"]),
            concepts=(ConceptSpec("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),),
        ),
        "cash": ProfilingMetricSpec(
            name="cash",
            period_kind="instant",
            units=frozenset(["USD"]),
            concepts=(ConceptSpec("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),),
        ),
        "debt": ProfilingMetricSpec(
            name="debt",
            period_kind="instant",
            units=frozenset(["USD"]),
            concepts=(ConceptSpec("us-gaap", "LongTermDebt"),),
        ),
        "shares": ProfilingMetricSpec(
            name="shares",
            period_kind="duration",
            units=frozenset(["shares"]),
            concepts=(ConceptSpec("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),),
        ),
    }
    return ProfilingConfig(
        required_metrics=tuple(required or ["revenue", "operating_income", "net_income",
                                            "operating_cash_flow", "cash", "debt", "shares"]),
        annual_forms=frozenset(["10-K", "10-K/A", "20-F"]),
        quarterly_forms=frozenset(["10-Q", "10-Q/A"]),
        metrics=metrics,
    )


def _profiler(required=None) -> CompanyFactsProfiler:
    return CompanyFactsProfiler(_minimal_config(required=required))


def _payload(facts: dict) -> bytes:
    return orjson.dumps({
        "cik": "CIK0000723125",
        "name": "ACME INC",
        "facts": facts,
    })


def _obs(end: str, val: float, form: str = "10-K", fp: str = "FY", fy: int = 2023,
         filed: str = "2023-10-17") -> dict:
    return {"end": end, "val": val, "form": form, "fp": fp, "fy": fy,
            "filed": filed, "accn": "0000000000-23-000001"}


def _profile(profiler, facts):
    return profiler.profile(
        company_id=_COMPANY_ID,
        cik="0000723125",
        payload=_payload(facts),
        profile_version=_VERSION,
        scanned_at=_NOW,
        source_run_id=_RUN_ID,
    )


# ── Metric detection ─────────────────────────────────────────────────────────


def test_has_revenue_when_matching_concept_present():
    p = _profiler()
    facts = {"us-gaap": {"Revenues": {"units": {"USD": [_obs("2023-08-31", 15e9)]}}}}
    result = _profile(p, facts)
    assert result.has_revenue is True


def test_has_no_revenue_when_tag_missing():
    p = _profiler()
    result = _profile(p, {})
    assert result.has_revenue is False


def test_all_seven_metrics_detected():
    p = _profiler()
    facts = {
        "us-gaap": {
            "Revenues": {"units": {"USD": [_obs("2023-08-31", 15e9)]}},
            "OperatingIncomeLoss": {"units": {"USD": [_obs("2023-08-31", 2e9)]}},
            "NetIncomeLoss": {"units": {"USD": [_obs("2023-08-31", 1.5e9)]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [_obs("2023-08-31", 3e9)]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_obs("2023-08-31", 5e9)]}},
            "LongTermDebt": {"units": {"USD": [_obs("2023-08-31", 10e9)]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {
                "units": {"shares": [_obs("2023-08-31", 1e9)]}
            },
        }
    }
    result = _profile(p, facts)
    assert result.has_revenue is True
    assert result.has_operating_income is True
    assert result.has_net_income is True
    assert result.has_operating_cash_flow is True
    assert result.has_cash is True
    assert result.has_debt is True
    assert result.has_shares is True


def test_wrong_unit_does_not_match():
    p = _profiler()
    # Revenues reported in shares (nonsense) — should NOT match revenue metric
    facts = {"us-gaap": {"Revenues": {"units": {"shares": [_obs("2023-08-31", 15e9)]}}}}
    result = _profile(p, facts)
    assert result.has_revenue is False


# ── Coverage calculation ─────────────────────────────────────────────────────


def test_canonical_metric_coverage_zero_when_no_metrics():
    p = _profiler()
    result = _profile(p, {})
    assert result.canonical_metric_coverage == 0.0


def test_canonical_metric_coverage_one_of_seven():
    p = _profiler()
    facts = {"us-gaap": {"Revenues": {"units": {"USD": [_obs("2023-08-31", 15e9)]}}}}
    result = _profile(p, facts)
    expected = round(1 / 7, 6)
    assert result.canonical_metric_coverage == expected


def test_canonical_metric_coverage_full():
    p = _profiler()
    facts = {
        "us-gaap": {
            "Revenues": {"units": {"USD": [_obs("2023-08-31", 15e9)]}},
            "OperatingIncomeLoss": {"units": {"USD": [_obs("2023-08-31", 2e9)]}},
            "NetIncomeLoss": {"units": {"USD": [_obs("2023-08-31", 1.5e9)]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [_obs("2023-08-31", 3e9)]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_obs("2023-08-31", 5e9)]}},
            "LongTermDebt": {"units": {"USD": [_obs("2023-08-31", 10e9)]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {
                "units": {"shares": [_obs("2023-08-31", 1e9)]}
            },
        }
    }
    result = _profile(p, facts)
    assert result.canonical_metric_coverage == 1.0


# ── Period counting ──────────────────────────────────────────────────────────


def test_annual_periods_counted():
    p = _profiler()
    facts = {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        _obs("2023-08-31", 15e9, form="10-K", fp="FY", fy=2023),
                        _obs("2022-09-01", 21e9, form="10-K", fp="FY", fy=2022),
                    ]
                }
            }
        }
    }
    result = _profile(p, facts)
    assert result.annual_periods == 2


def test_quarterly_periods_counted():
    p = _profiler()
    facts = {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        _obs("2023-03-02", 3.7e9, form="10-Q", fp="Q2", fy=2023),
                        _obs("2023-06-01", 3.5e9, form="10-Q", fp="Q3", fy=2023),
                    ]
                }
            }
        }
    }
    result = _profile(p, facts)
    assert result.quarterly_periods == 2


def test_duplicate_periods_deduplicated():
    p = _profiler()
    # Same (fy=2023, end=2023-08-31) for two different metrics — counts once.
    facts = {
        "us-gaap": {
            "Revenues": {"units": {"USD": [_obs("2023-08-31", 15e9)]}},
            "NetIncomeLoss": {"units": {"USD": [_obs("2023-08-31", 1.5e9)]}},
        }
    }
    result = _profile(p, facts)
    assert result.annual_periods == 1


# ── Date tracking ────────────────────────────────────────────────────────────


def test_first_and_latest_period_end_tracked():
    p = _profiler()
    facts = {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        _obs("2021-09-02", 27e9, fp="FY", fy=2021),
                        _obs("2023-08-31", 15e9, fp="FY", fy=2023),
                    ]
                }
            }
        }
    }
    result = _profile(p, facts)
    assert result.first_period_end == date(2021, 9, 2)
    assert result.latest_period_end == date(2023, 8, 31)


def test_latest_filed_date_tracked():
    p = _profiler()
    facts = {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {**_obs("2023-08-31", 15e9), "filed": "2023-10-17"},
                        {**_obs("2022-09-01", 21e9), "filed": "2022-10-14"},
                    ]
                }
            }
        }
    }
    result = _profile(p, facts)
    assert result.latest_filed_date == date(2023, 10, 17)


# ── Fact count and malformed ─────────────────────────────────────────────────


def test_fact_count_includes_all_valid_observations():
    p = _profiler()
    facts = {
        "us-gaap": {
            "Revenues": {"units": {"USD": [_obs("2023-08-31", 15e9), _obs("2022-09-01", 21e9)]}},
            "NetIncomeLoss": {"units": {"USD": [_obs("2023-08-31", 1.5e9)]}},
        }
    }
    result = _profile(p, facts)
    assert result.fact_count == 3


def test_malformed_observation_not_counted_in_fact_count():
    p = _profiler()
    facts = {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {"end": "not-a-date", "val": 1e9},   # malformed
                        _obs("2023-08-31", 15e9),             # valid
                    ]
                }
            }
        }
    }
    result = _profile(p, facts)
    assert result.fact_count == 1
    assert result.quality_flags["malformed_fact_count"] == 1


# ── profile_missing ──────────────────────────────────────────────────────────


def test_profile_missing_returns_zero_coverage():
    p = _profiler()
    result = p.profile_missing(
        company_id=_COMPANY_ID,
        cik="0000723125",
        profile_version=_VERSION,
        scanned_at=_NOW,
        source_run_id=_RUN_ID,
    )
    assert result.has_revenue is False
    assert result.canonical_metric_coverage == 0.0
    assert result.annual_periods == 0
    assert result.fact_count == 0
    assert result.quality_flags["companyfacts_member_missing"] is True


# ── CIK mismatch flag ────────────────────────────────────────────────────────


def test_cik_mismatch_flag_in_quality_flags():
    p = _profiler()
    result = p.profile(
        company_id=_COMPANY_ID,
        cik="0000723125",
        payload=_payload({}),
        profile_version=_VERSION,
        scanned_at=_NOW,
        source_run_id=_RUN_ID,
        cik_mismatch=True,
    )
    assert result.quality_flags["cik_mismatch"] is True


def test_no_cik_mismatch_by_default():
    p = _profiler()
    result = _profile(p, {})
    assert result.quality_flags["cik_mismatch"] is False


# ── Integration: ProfilingConfig.from_canonical_metrics ─────────────────────


def test_profiling_config_loads_from_canonical_metrics_yaml():
    from invest_ml.config.loaders import load_canonical_metrics
    from invest_ml.sec.profiler import ProfilingConfig

    cfg = load_canonical_metrics()
    config = ProfilingConfig.from_canonical_metrics(cfg)

    assert "revenue" in config.metrics
    assert "debt" in config.metrics
    assert "shares" in config.metrics
    assert len(config.required_metrics) == 7
    assert "10-K" in config.annual_forms
    assert "10-Q" in config.quarterly_forms
