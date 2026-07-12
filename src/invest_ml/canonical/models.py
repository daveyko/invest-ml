"""Domain dataclasses for canonical metric normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ConceptConfig:
    taxonomy: str
    tag: str
    priority: int  # lower = preferred


@dataclass(frozen=True)
class MetricConfig:
    name: str
    period_kind: str  # "duration" | "instant"
    expected_units: tuple[str, ...]
    concepts: tuple[ConceptConfig, ...]


@dataclass(frozen=True)
class PeriodClassification:
    period_type: str  # "annual" | "quarter" | "unsupported"
    supported: bool
    reason: str
    duration_days: int | None


@dataclass(frozen=True)
class CandidateFact:
    fact_id: str
    company_id: UUID
    taxonomy: str
    tag: str
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    accession_number: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    filed_date: date


@dataclass(frozen=True)
class ResolvedCanonicalMetric:
    company_id: UUID
    metric_name: str
    period_type: str
    fiscal_year: int | None
    fiscal_period: str | None
    period_start: date | None
    period_end: date
    available_at: date
    value: Decimal
    unit: str
    normalization_version: str
    source_fact_ids: list[str]
    derivation: dict[str, Any]
    quality_flags: dict[str, Any]


@dataclass(frozen=True)
class CanonicalMetricInsertResult:
    rows_seen: int
    rows_inserted: int
    rows_already_present: int
    conflicting_rows: int


@dataclass
class CanonicalMetricNormalizationResult:
    normalization_version: str
    configuration_hash: str
    companies_considered: int
    companies_with_metrics: int
    metrics_resolved: int
    metrics_created: int
    metrics_already_present: int
    annual_metrics_resolved: int
    quarterly_metrics_resolved: int
    facts_considered: int
    facts_rejected: int
    candidate_groups: int
    coverage: dict[str, dict[str, int]] = field(default_factory=dict)
