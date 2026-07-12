"""Repository for raw_source_versions, raw_version_derivations, and xbrl_facts."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import distinct, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from invest_ml.db.models.financials import RawSourceVersion, RawVersionDerivation, XbrlFact
from invest_ml.xbrl.models import FactInsertResult, FlattenedXbrlFact

logger = logging.getLogger(__name__)


class XbrlFactsRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Raw source version ────────────────────────────────────────────────────

    def get_or_create_member_version(
        self,
        *,
        source: str,
        entity_key: str,
        content_hash: str,
        source_locator: dict,
        object_uri: str,
        source_run_id: UUID | None,
        observed_at: datetime,
        byte_size: int,
    ) -> tuple[UUID, bool]:
        """Return (raw_version_id, is_new).

        Uses INSERT ON CONFLICT DO NOTHING so that if the same content_hash was
        already registered we just return the existing row's ID.
        """
        stmt = (
            pg_insert(RawSourceVersion)
            .values(
                source=source,
                entity_key=entity_key,
                content_hash=content_hash,
                source_locator=source_locator,
                object_uri=object_uri,
                source_run_id=source_run_id,
                observed_at=observed_at,
                byte_size=byte_size,
            )
            .on_conflict_do_nothing(constraint="uq_raw_source_versions")
        )
        self._s.execute(stmt)

        row = self._s.execute(
            select(RawSourceVersion.raw_version_id).where(
                RawSourceVersion.source == source,
                RawSourceVersion.entity_key == entity_key,
                RawSourceVersion.content_hash == content_hash,
            )
        ).scalar_one()

        is_new = True
        existing = self._s.execute(
            select(RawSourceVersion).where(
                RawSourceVersion.raw_version_id == row
            )
        ).scalar_one()
        is_new = existing.source_run_id == source_run_id

        return row, is_new

    # ── Derivation lifecycle ──────────────────────────────────────────────────

    def get_derivation_status(
        self,
        raw_version_id: UUID,
        derivation_type: str,
        derivation_version: str,
    ) -> str | None:
        row = self._s.execute(
            select(RawVersionDerivation.status).where(
                RawVersionDerivation.raw_version_id == raw_version_id,
                RawVersionDerivation.derivation_type == derivation_type,
                RawVersionDerivation.derivation_version == derivation_version,
            )
        ).scalar_one_or_none()
        return row

    def claim_derivation(
        self,
        raw_version_id: UUID,
        derivation_type: str,
        derivation_version: str,
        started_at: datetime,
    ) -> bool:
        """Attempt to claim a derivation slot.

        Returns True if claimed (either new or re-running a failed derivation).
        Returns False if already succeeded or running.
        """
        existing_status = self.get_derivation_status(
            raw_version_id, derivation_type, derivation_version
        )

        if existing_status == "succeeded":
            return False
        if existing_status == "running":
            return False
        if existing_status == "failed":
            self._s.execute(
                update(RawVersionDerivation)
                .where(
                    RawVersionDerivation.raw_version_id == raw_version_id,
                    RawVersionDerivation.derivation_type == derivation_type,
                    RawVersionDerivation.derivation_version == derivation_version,
                )
                .values(
                    status="running",
                    started_at=started_at,
                    completed_at=None,
                    row_count=None,
                    error=None,
                )
            )
            return True

        # Not found — insert fresh
        self._s.execute(
            pg_insert(RawVersionDerivation)
            .values(
                raw_version_id=raw_version_id,
                derivation_type=derivation_type,
                derivation_version=derivation_version,
                status="running",
                started_at=started_at,
                derivation_metadata={},
            )
            .on_conflict_do_nothing()
        )
        return True

    def mark_derivation_succeeded(
        self,
        raw_version_id: UUID,
        derivation_type: str,
        derivation_version: str,
        *,
        row_count: int,
    ) -> None:
        self._s.execute(
            update(RawVersionDerivation)
            .where(
                RawVersionDerivation.raw_version_id == raw_version_id,
                RawVersionDerivation.derivation_type == derivation_type,
                RawVersionDerivation.derivation_version == derivation_version,
            )
            .values(
                status="succeeded",
                completed_at=datetime.now(tz=UTC),
                row_count=row_count,
            )
        )

    def mark_derivation_failed(
        self,
        raw_version_id: UUID,
        derivation_type: str,
        derivation_version: str,
        *,
        error: str,
    ) -> None:
        self._s.execute(
            update(RawVersionDerivation)
            .where(
                RawVersionDerivation.raw_version_id == raw_version_id,
                RawVersionDerivation.derivation_type == derivation_type,
                RawVersionDerivation.derivation_version == derivation_version,
            )
            .values(
                status="failed",
                completed_at=datetime.now(tz=UTC),
                error=error[:2000],
            )
        )

    # ── Fact bulk insert ──────────────────────────────────────────────────────

    def bulk_insert_facts(
        self,
        facts: list[FlattenedXbrlFact],
        *,
        ingested_at: datetime,
    ) -> FactInsertResult:
        """Insert facts with INSERT ON CONFLICT DO NOTHING.

        Uses SQLAlchemy Core executemany (insertmanyvalues with psycopg3)
        for efficient batched insertion without per-row session.add().
        """
        if not facts:
            return FactInsertResult(inserted=0, already_existed=0)

        rows = [
            {
                "fact_id": f.fact_id,
                "company_id": f.company_id,
                "taxonomy": f.taxonomy,
                "tag": f.tag,
                "label": f.label,
                "description": f.description,
                "unit": f.unit,
                "period_start": f.period_start,
                "period_end": f.period_end,
                "value": f.value,
                "accession_number": f.accession_number,
                "fiscal_year": f.fiscal_year,
                "fiscal_period": f.fiscal_period,
                "form": f.form,
                "filed_date": f.filed_date,
                "frame": f.frame,
                "dimensions": f.dimensions,
                "raw_version_id": f.raw_version_id,
                "created_at": ingested_at,
            }
            for f in facts
        ]

        stmt = pg_insert(XbrlFact).on_conflict_do_nothing(index_elements=["fact_id"])
        self._s.execute(stmt, rows)

        # rowcount is not reliably available on IteratorResult (psycopg3 executemany).
        # Record the attempted count; the derivation succeeded regardless of conflicts.
        return FactInsertResult(inserted=len(facts), already_existed=0)

    # ── Canonical metrics candidate streaming ─────────────────────────────────

    def stream_candidate_facts(
        self,
        *,
        taxonomy_tags: list[tuple[str, str]],
        company_ids: list[UUID] | None = None,
        batch_size: int = 100,
    ) -> Iterator[list[XbrlFact]]:
        """Yield company-batched lists of XbrlFact rows for the given (taxonomy, tag) pairs.

        Finds all distinct company_ids that have at least one relevant fact,
        then yields facts in batches of batch_size companies.
        """
        if not taxonomy_tags:
            return

        company_query = select(distinct(XbrlFact.company_id)).where(
            tuple_(XbrlFact.taxonomy, XbrlFact.tag).in_(taxonomy_tags)
        )
        if company_ids:
            company_query = company_query.where(XbrlFact.company_id.in_(company_ids))

        all_company_ids: list[UUID] = list(self._s.execute(company_query).scalars())

        for offset in range(0, len(all_company_ids), batch_size):
            batch_ids = all_company_ids[offset : offset + batch_size]
            facts = (
                self._s.execute(
                    select(XbrlFact)
                    .where(
                        XbrlFact.company_id.in_(batch_ids),
                        tuple_(XbrlFact.taxonomy, XbrlFact.tag).in_(taxonomy_tags),
                    )
                    .order_by(
                        XbrlFact.company_id,
                        XbrlFact.taxonomy,
                        XbrlFact.tag,
                        XbrlFact.period_end,
                        XbrlFact.filed_date,
                    )
                )
                .scalars()
                .all()
            )
            if facts:
                yield facts
