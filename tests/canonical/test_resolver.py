"""Tests for CandidateResolver."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from invest_ml.canonical.models import (
    CandidateFact,
    ConceptConfig,
    MetricConfig,
    PeriodClassification,
)
from invest_ml.canonical.resolver import CandidateResolver


def _make_resolver() -> CandidateResolver:
    return CandidateResolver(annual_duration_center=365.0, quarterly_duration_center=90.0)


def _make_concept(priority: int = 1, taxonomy: str = "us-gaap", tag: str = "Revenues") -> ConceptConfig:
    return ConceptConfig(taxonomy=taxonomy, tag=tag, priority=priority)


def _make_fact(
    *,
    fact_id: str = "abc",
    unit: str = "USD",
    form: str = "10-K",
    accession_number: str = "0001-24-000001",
    fiscal_year: int = 2023,
    fiscal_period: str = "FY",
    value: Decimal = Decimal("1000000"),
    period_start: date | None = date(2023, 1, 1),
    period_end: date = date(2023, 12, 31),
    filed_date: date = date(2024, 2, 15),
    taxonomy: str = "us-gaap",
    tag: str = "Revenues",
) -> CandidateFact:
    return CandidateFact(
        fact_id=fact_id,
        company_id=uuid4(),
        taxonomy=taxonomy,
        tag=tag,
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        value=value,
        accession_number=accession_number,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        form=form,
        filed_date=filed_date,
    )


def _annual_classification(duration_days: int = 364) -> PeriodClassification:
    return PeriodClassification(
        period_type="annual", supported=True, reason="duration/annual", duration_days=duration_days
    )


def _quarter_classification(duration_days: int = 90) -> PeriodClassification:
    return PeriodClassification(
        period_type="quarter", supported=True, reason="duration/quarter", duration_days=duration_days
    )


def _metric_config(expected_units: list[str] = None) -> MetricConfig:
    return MetricConfig(
        name="revenue",
        period_kind="duration",
        expected_units=tuple(expected_units or ["USD"]),
        concepts=(ConceptConfig("us-gaap", "Revenues", 1),),
    )


def test_resolve_single_candidate_returns_it():
    resolver = _make_resolver()
    fact = _make_fact()
    concept = _make_concept()
    classification = _annual_classification()

    winner, winner_concept, flags = resolver.resolve(
        [(fact, concept, classification)],
        metric_config=_metric_config(),
        period_type="annual",
    )
    assert winner is fact
    assert winner_concept is concept
    assert flags["candidate_fact_count"] == 1
    assert flags["unit_exact_match"] is True
    assert flags["had_amendment_candidates"] is False


def test_resolve_prefers_lower_priority_concept():
    resolver = _make_resolver()
    concept_p1 = ConceptConfig("us-gaap", "RevenueFromContract", 1)
    concept_p2 = ConceptConfig("us-gaap", "Revenues", 2)
    fact_p1 = _make_fact(fact_id="p1", tag="RevenueFromContract")
    fact_p2 = _make_fact(fact_id="p2", tag="Revenues")
    cls = _annual_classification()

    winner, winner_concept, _ = resolver.resolve(
        [(fact_p1, concept_p1, cls), (fact_p2, concept_p2, cls)],
        metric_config=_metric_config(),
        period_type="annual",
    )
    assert winner.fact_id == "p1"
    assert winner_concept.priority == 1


def test_resolve_prefers_non_amendment_form():
    resolver = _make_resolver()
    concept = _make_concept()
    fact_orig = _make_fact(fact_id="orig", form="10-K")
    fact_amend = _make_fact(fact_id="amend", form="10-K/A")
    cls = _annual_classification()

    winner, _, flags = resolver.resolve(
        [(fact_orig, concept, cls), (fact_amend, concept, cls)],
        metric_config=_metric_config(),
        period_type="annual",
    )
    assert winner.fact_id == "orig"
    assert flags["had_amendment_candidates"] is True


def test_resolve_duration_fit_prefers_closer_to_center():
    resolver = _make_resolver()
    concept = _make_concept()
    # 364 days is closer to center 365 than 300 days
    fact_close = _make_fact(fact_id="close", period_start=date(2023, 1, 2))  # 363 days
    fact_far = _make_fact(fact_id="far", period_start=date(2023, 3, 8))  # 298 days
    cls_close = _annual_classification(duration_days=363)
    cls_far = _annual_classification(duration_days=298)

    winner, _, _ = resolver.resolve(
        [(fact_close, concept, cls_close), (fact_far, concept, cls_far)],
        metric_config=_metric_config(),
        period_type="annual",
    )
    assert winner.fact_id == "close"


def test_resolve_fact_id_as_tiebreaker():
    resolver = _make_resolver()
    concept = _make_concept()
    cls = _annual_classification()
    fact_a = _make_fact(fact_id="aaa", accession_number="0001")
    fact_b = _make_fact(fact_id="bbb", accession_number="0001")

    winner, _, _ = resolver.resolve(
        [(fact_a, concept, cls), (fact_b, concept, cls)],
        metric_config=_metric_config(),
        period_type="annual",
    )
    assert winner.fact_id == "aaa"


def test_resolve_empty_candidates_raises():
    resolver = _make_resolver()
    with pytest.raises(ValueError, match="empty candidate list"):
        resolver.resolve([], metric_config=_metric_config(), period_type="annual")


def test_resolve_quality_flags_populated():
    resolver = _make_resolver()
    fact = _make_fact(form="10-K", accession_number="0001-24-000001")
    concept = _make_concept(priority=1)
    cls = _annual_classification(duration_days=364)

    _, _, flags = resolver.resolve(
        [(fact, concept, cls)],
        metric_config=_metric_config(),
        period_type="annual",
    )
    assert flags["selected_form"] == "10-K"
    assert flags["selected_accession_number"] == "0001-24-000001"
    assert flags["concept_priority"] == 1
    assert flags["duration_days"] == 364
    assert flags["unit_exact_match"] is True
    assert flags["had_amendment_candidates"] is False


def test_resolve_unit_mismatch_penalized():
    resolver = _make_resolver()
    concept = _make_concept()
    fact_usd = _make_fact(fact_id="usd", unit="USD")
    fact_eur = _make_fact(fact_id="eur", unit="EUR")
    cls = _annual_classification()

    winner, _, flags = resolver.resolve(
        [(fact_usd, concept, cls), (fact_eur, concept, cls)],
        metric_config=_metric_config(expected_units=["USD"]),
        period_type="annual",
    )
    assert winner.fact_id == "usd"
    assert flags["unit_exact_match"] is True


def test_resolve_is_deterministic_regardless_of_input_order():
    resolver = _make_resolver()
    concept_p1 = ConceptConfig("us-gaap", "Tag1", 1)
    concept_p2 = ConceptConfig("us-gaap", "Tag2", 2)
    fact1 = _make_fact(fact_id="z_fact", tag="Tag1")
    fact2 = _make_fact(fact_id="a_fact", tag="Tag2")
    cls = _annual_classification()
    mc = _metric_config()

    winner1, _, _ = resolver.resolve(
        [(fact1, concept_p1, cls), (fact2, concept_p2, cls)],
        metric_config=mc, period_type="annual",
    )
    winner2, _, _ = resolver.resolve(
        [(fact2, concept_p2, cls), (fact1, concept_p1, cls)],
        metric_config=mc, period_type="annual",
    )
    assert winner1.fact_id == winner2.fact_id
