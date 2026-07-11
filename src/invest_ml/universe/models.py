"""Domain models for universe evaluation.

Pure Python dataclasses — no SQLAlchemy, no network, no DB imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True)
class CandidateSecurity:
    """A single ticker/exchange pair under consideration."""

    security_id: UUID
    ticker: str
    exchange: str | None
    normalized_exchange: str | None
    currently_observed: bool


@dataclass(frozen=True)
class CandidateCompanyInput:
    """All data needed to evaluate one company — passed to the evaluator without DB access."""

    company_id: UUID
    cik: str
    legal_name: str
    entity_type: str | None
    latest_filing_date: date | None
    sic_codes: tuple[str, ...]
    has_current_data_profile: bool
    securities: tuple[CandidateSecurity, ...]


@dataclass(frozen=True)
class CandidateDecision:
    """Evaluation result for a single company."""

    company_id: UUID
    included: bool
    inclusion_reasons: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    eligible_securities: tuple[CandidateSecurity, ...]


@dataclass(frozen=True)
class CandidateUniverseResult:
    """Aggregate statistics returned by CandidateUniverseService.materialize()."""

    evaluated_companies: int
    included_companies: int
    newly_included: int
    already_included: int
    newly_excluded: int
    exclusion_counts: dict[str, int]
    universe_id: UUID
    criteria_hash: str
