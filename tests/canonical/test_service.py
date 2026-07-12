"""Tests for CanonicalMetricNormalizationService using synthetic in-memory data."""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from invest_ml.canonical.models import CandidateFact, ResolvedCanonicalMetric
from invest_ml.canonical.registry import CanonicalMetricRegistry
from invest_ml.canonical.service import CanonicalMetricNormalizationService

_CFG = {
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
                {"taxonomy": "us-gaap", "tag": "Revenues", "priority": 1},
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

_REGISTRY = CanonicalMetricRegistry.from_config(_CFG)


def _make_xbrl_fact(
    *,
    company_id: UUID,
    taxonomy: str = "us-gaap",
    tag: str = "Revenues",
    unit: str = "USD",
    period_start: date | None = date(2023, 1, 1),
    period_end: date = date(2023, 12, 31),
    value: Decimal = Decimal("1000000"),
    form: str = "10-K",
    fiscal_period: str = "FY",
    fiscal_year: int = 2023,
    filed_date: date = date(2024, 2, 15),
    fact_id: str | None = None,
    accession_number: str | None = "0001-24-000001",
):
    m = MagicMock()
    m.fact_id = fact_id or f"{tag}_{period_end}_{company_id}"
    m.company_id = company_id
    m.taxonomy = taxonomy
    m.tag = tag
    m.unit = unit
    m.period_start = period_start
    m.period_end = period_end
    m.value = value
    m.form = form
    m.fiscal_period = fiscal_period
    m.fiscal_year = fiscal_year
    m.filed_date = filed_date
    m.accession_number = accession_number
    return m


def _make_service() -> CanonicalMetricNormalizationService:
    session_factory = MagicMock()
    return CanonicalMetricNormalizationService(session_factory=session_factory)


def test_normalize_company_duration_annual():
    service = _make_service()
    company_id = uuid4()
    facts = [_make_xbrl_fact(company_id=company_id, tag="Revenues")]

    from invest_ml.canonical.classifier import CanonicalPeriodClassifier
    from invest_ml.canonical.resolver import CandidateResolver

    classifier = CanonicalPeriodClassifier(_REGISTRY)
    resolver = CandidateResolver(annual_duration_center=365.0, quarterly_duration_center=90.0)

    fc, fr, cg, resolved = service._normalize_company(
        company_id=company_id,
        facts=facts,
        configuration=_REGISTRY,
        classifier=classifier,
        resolver=resolver,
        normalization_version="canonical_metrics_v1",
    )

    assert fc == 1
    assert fr == 0
    assert cg == 1
    assert len(resolved) == 1
    r = resolved[0]
    assert r.metric_name == "revenue"
    assert r.period_type == "annual"
    assert r.value == Decimal("1000000")
    assert r.available_at == date(2024, 2, 15)
    assert r.derivation["type"] == "direct_fact"
    assert r.derivation["tag"] == "Revenues"


def test_normalize_company_instant_annual():
    service = _make_service()
    company_id = uuid4()
    facts = [
        _make_xbrl_fact(
            company_id=company_id,
            tag="Assets",
            period_start=None,
            value=Decimal("5000000"),
        )
    ]

    from invest_ml.canonical.classifier import CanonicalPeriodClassifier
    from invest_ml.canonical.resolver import CandidateResolver

    classifier = CanonicalPeriodClassifier(_REGISTRY)
    resolver = CandidateResolver(annual_duration_center=365.0, quarterly_duration_center=90.0)

    _, _, _, resolved = service._normalize_company(
        company_id=company_id,
        facts=facts,
        configuration=_REGISTRY,
        classifier=classifier,
        resolver=resolver,
        normalization_version="canonical_metrics_v1",
    )

    assert len(resolved) == 1
    assert resolved[0].metric_name == "total_assets"
    assert resolved[0].period_type == "annual"


def test_normalize_company_rejects_wrong_unit():
    service = _make_service()
    company_id = uuid4()
    facts = [_make_xbrl_fact(company_id=company_id, tag="Revenues", unit="EUR")]

    from invest_ml.canonical.classifier import CanonicalPeriodClassifier
    from invest_ml.canonical.resolver import CandidateResolver

    classifier = CanonicalPeriodClassifier(_REGISTRY)
    resolver = CandidateResolver(annual_duration_center=365.0, quarterly_duration_center=90.0)

    fc, fr, cg, resolved = service._normalize_company(
        company_id=company_id,
        facts=facts,
        configuration=_REGISTRY,
        classifier=classifier,
        resolver=resolver,
        normalization_version="canonical_metrics_v1",
    )

    assert fr == 1
    assert resolved == []


def test_normalize_company_rejects_unsupported_period():
    service = _make_service()
    company_id = uuid4()
    # S-1 is not in any allowed form list
    facts = [_make_xbrl_fact(company_id=company_id, tag="Revenues", form="S-1")]

    from invest_ml.canonical.classifier import CanonicalPeriodClassifier
    from invest_ml.canonical.resolver import CandidateResolver

    classifier = CanonicalPeriodClassifier(_REGISTRY)
    resolver = CandidateResolver(annual_duration_center=365.0, quarterly_duration_center=90.0)

    _, fr, _, resolved = service._normalize_company(
        company_id=company_id,
        facts=facts,
        configuration=_REGISTRY,
        classifier=classifier,
        resolver=resolver,
        normalization_version="canonical_metrics_v1",
    )

    assert fr == 1
    assert resolved == []


def test_normalize_company_groups_same_period_separately():
    """Two facts for the same period_end but different filed_dates are separate groups."""
    service = _make_service()
    company_id = uuid4()
    fact_original = _make_xbrl_fact(
        company_id=company_id, tag="Revenues", fact_id="f1",
        filed_date=date(2024, 2, 15),
    )
    fact_amended = _make_xbrl_fact(
        company_id=company_id, tag="Revenues", fact_id="f2",
        filed_date=date(2024, 3, 10), form="10-K/A",
    )

    from invest_ml.canonical.classifier import CanonicalPeriodClassifier
    from invest_ml.canonical.resolver import CandidateResolver

    classifier = CanonicalPeriodClassifier(_REGISTRY)
    resolver = CandidateResolver(annual_duration_center=365.0, quarterly_duration_center=90.0)

    _, _, cg, resolved = service._normalize_company(
        company_id=company_id,
        facts=[fact_original, fact_amended],
        configuration=_REGISTRY,
        classifier=classifier,
        resolver=resolver,
        normalization_version="canonical_metrics_v1",
    )

    # Different filed_dates → different available_at → different groups
    assert cg == 2
    assert len(resolved) == 2


def test_normalize_company_derivation_contains_config_hash():
    service = _make_service()
    company_id = uuid4()
    facts = [_make_xbrl_fact(company_id=company_id, tag="Revenues")]

    from invest_ml.canonical.classifier import CanonicalPeriodClassifier
    from invest_ml.canonical.resolver import CandidateResolver

    classifier = CanonicalPeriodClassifier(_REGISTRY)
    resolver = CandidateResolver(annual_duration_center=365.0, quarterly_duration_center=90.0)

    _, _, _, resolved = service._normalize_company(
        company_id=company_id,
        facts=facts,
        configuration=_REGISTRY,
        classifier=classifier,
        resolver=resolver,
        normalization_version="canonical_metrics_v1",
    )

    assert len(resolved) == 1
    assert "configuration_hash" in resolved[0].derivation
    assert resolved[0].derivation["configuration_hash"] == _REGISTRY.configuration_hash
