"""Domain dataclasses for XBRL facts ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class SelectedCompany:
    """A training-universe member targeted for XBRL ingestion."""

    company_id: UUID
    cik: str  # 10-digit zero-padded
    legal_name: str


@dataclass(frozen=True)
class FlattenedXbrlFact:
    """One observation from a CompanyFacts JSON member, ready for persistence."""

    fact_id: str  # deterministic SHA-256
    company_id: UUID
    taxonomy: str
    tag: str
    label: str | None
    description: str | None
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    accession_number: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    filed_date: date
    frame: str | None
    dimensions: dict
    raw_version_id: UUID


@dataclass(frozen=True)
class XbrlFactsIngestPlan:
    """Pre-flight summary before processing begins."""

    archive_path: Path
    archive_sha256: str
    target_ciks: frozenset
    found_ciks: frozenset
    members_total: int
    derivation_type: str
    derivation_version: str


@dataclass(frozen=True)
class FactInsertResult:
    inserted: int
    already_existed: int


@dataclass(frozen=True)
class MemberIngestionResult:
    cik: str
    succeeded: bool
    facts_inserted: int
    facts_already_existed: int
    skipped_reason: str | None  # "already_succeeded", "not_found", etc.
    error: str | None


@dataclass(frozen=True)
class XbrlFactsIngestionResult:
    members_processed: int
    members_succeeded: int
    members_failed: int
    members_skipped_not_found: int
    members_skipped_already_done: int
    total_facts_inserted: int
    total_facts_already_existed: int
    derivation_version: str
