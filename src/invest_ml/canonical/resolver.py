"""Deterministic candidate resolver: picks the best fact within a candidate group."""

from __future__ import annotations

from dataclasses import dataclass

from invest_ml.canonical.models import (
    CandidateFact,
    ConceptConfig,
    MetricConfig,
    PeriodClassification,
)

_Candidate = tuple[CandidateFact, ConceptConfig, PeriodClassification]


@dataclass(frozen=True)
class _Scored:
    sort_key: tuple
    fact: CandidateFact
    concept: ConceptConfig
    classification: PeriodClassification


class CandidateResolver:
    """Select one winner from a group of competing candidate facts.

    Sort key (ascending = better):
      1. Concept priority (lower = more preferred taxonomy/tag)
      2. Unit mismatch penalty (exact match = 0, other = 1)
      3. Amendment penalty (non-amendment = 0, /A form = 1)
      4. Duration fit (|days - center| — 0 for instant)
      5. Accession number (lexicographic, for determinism)
      6. Fact ID (lexicographic, final tie-breaker)
    """

    def __init__(
        self,
        *,
        annual_duration_center: float,
        quarterly_duration_center: float,
    ) -> None:
        self._annual_center = annual_duration_center
        self._quarterly_center = quarterly_duration_center

    def resolve(
        self,
        candidates: list[_Candidate],
        *,
        metric_config: MetricConfig,
        period_type: str,
    ) -> tuple[CandidateFact, ConceptConfig, dict]:
        """Return (winner_fact, winner_concept, quality_flags).

        Raises ValueError if candidates is empty.
        """
        if not candidates:
            raise ValueError("resolve() called with empty candidate list")

        scored = [
            self._score(c, metric_config=metric_config, period_type=period_type)
            for c in candidates
        ]
        winner = min(scored, key=lambda s: s.sort_key)

        had_amendment = any((c[0].form or "").endswith("/A") for c in candidates)

        quality_flags: dict = {
            "candidate_fact_count": len(candidates),
            "selected_form": winner.fact.form,
            "selected_accession_number": winner.fact.accession_number,
            "selected_taxonomy": winner.fact.taxonomy,
            "selected_tag": winner.fact.tag,
            "concept_priority": winner.concept.priority,
            "duration_days": winner.classification.duration_days,
            "had_amendment_candidates": had_amendment,
            "unit_exact_match": winner.fact.unit in metric_config.expected_units,
        }

        return winner.fact, winner.concept, quality_flags

    def _score(
        self,
        candidate: _Candidate,
        *,
        metric_config: MetricConfig,
        period_type: str,
    ) -> _Scored:
        fact, concept, classification = candidate

        priority = concept.priority
        unit_mismatch = 0 if fact.unit in metric_config.expected_units else 1
        is_amendment = 1 if (fact.form or "").endswith("/A") else 0

        if classification.duration_days is not None:
            center = (
                self._annual_center if period_type == "annual" else self._quarterly_center
            )
            duration_fit: float = abs(classification.duration_days - center)
        else:
            duration_fit = 0.0

        accession = fact.accession_number or ""
        sort_key = (priority, unit_mismatch, is_amendment, duration_fit, accession, fact.fact_id)

        return _Scored(sort_key=sort_key, fact=fact, concept=concept, classification=classification)
