"""SQLAlchemy repository for the company catalog.

Methods are domain-specific; there is no generic CRUD abstraction.
All writes use INSERT ... ON CONFLICT DO UPDATE (upsert) to make
every operation idempotent.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from invest_ml.db.models.classification import CompanyClassification
from invest_ml.db.models.company import Company, Security
from invest_ml.db.models.ingestion import IngestionRun

logger = logging.getLogger(__name__)

_SIC_TAXONOMY = "sec_sic"
_SIC_SOURCE = "sec"
_SIC_CLASSIFIER_VERSION = "sec_submissions_v1"
_SOURCE = "sec_submissions_bulk"


class CompanyCatalogRepository:
    """Provides idempotent write operations for catalog data.

    All methods operate within the provided session; callers are responsible
    for committing or rolling back.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Ingestion run ─────────────────────────────────────────────────────────

    def create_ingestion_run(
        self,
        source: str,
        source_uri: str,
        started_at: datetime,
    ) -> IngestionRun:
        run = IngestionRun(
            source=source,
            source_uri=source_uri,
            started_at=started_at,
            status="running",
            entities_checked=0,
            entities_changed=0,
            run_metadata={},
        )
        self._s.add(run)
        self._s.flush()
        return run

    def succeed_ingestion_run(
        self,
        run_id: UUID,
        *,
        archive_hash: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        entities_checked: int = 0,
        entities_changed: int = 0,
        extra_metadata: dict | None = None,
    ) -> None:
        self._s.execute(
            update(IngestionRun)
            .where(IngestionRun.run_id == run_id)
            .values(
                status="succeeded",
                completed_at=datetime.now(tz=UTC),
                archive_hash=archive_hash,
                etag=etag,
                last_modified=last_modified,
                entities_checked=entities_checked,
                entities_changed=entities_changed,
                run_metadata=extra_metadata or {},
            )
        )

    def fail_ingestion_run(self, run_id: UUID, *, error: str) -> None:
        self._s.execute(
            update(IngestionRun)
            .where(IngestionRun.run_id == run_id)
            .values(
                status="failed",
                completed_at=datetime.now(tz=UTC),
                error=error[:2000],  # cap to avoid very long stack traces in DB
            )
        )

    def find_latest_successful_ingestion_run(self, source: str) -> IngestionRun | None:
        """Return the most recent succeeded run for this source."""
        return self._s.execute(
            select(IngestionRun)
            .where(IngestionRun.source == source, IngestionRun.status == "succeeded")
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    # ── Company ──────────────────────────────────────────────────────────────

    def upsert_company(
        self,
        *,
        cik: str,
        legal_name: str,
        entity_type: str | None,
        filer_category: str | None,
        fiscal_year_end: str | None,
        state_of_incorporation: str | None,
        latest_filing_date: date | None,
        observed_at: datetime,
        source_run_id: UUID,
    ) -> tuple[Company, bool]:
        """Insert or update a company by CIK.

        Returns (company, inserted) where inserted=True means a new row was created.
        Preserves first_observed_at; always updates last_observed_at.
        """
        stmt = (
            pg_insert(Company)
            .values(
                cik=cik,
                legal_name=legal_name,
                entity_type=entity_type,
                filer_category=filer_category,
                fiscal_year_end=fiscal_year_end,
                state_of_incorporation=state_of_incorporation,
                latest_filing_date=latest_filing_date,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
                source_run_id=source_run_id,
            )
            .on_conflict_do_update(
                index_elements=["cik"],
                set_={
                    "legal_name": legal_name,
                    "entity_type": entity_type,
                    "filer_category": filer_category,
                    "fiscal_year_end": fiscal_year_end,
                    "state_of_incorporation": state_of_incorporation,
                    "latest_filing_date": latest_filing_date,
                    "last_observed_at": observed_at,
                    "source_run_id": source_run_id,
                },
            )
            .returning(Company)
        )
        result = self._s.execute(stmt)
        row = result.fetchone()
        # Determine if it was an insert vs update by comparing first/last observed times.
        # PostgreSQL returns the row after either branch; check if first == last (insert).
        company = row[0]
        inserted = company.first_observed_at >= observed_at
        return company, inserted

    def get_company_by_cik(self, cik: str) -> Company | None:
        return self._s.execute(
            select(Company).where(Company.cik == cik)
        ).scalar_one_or_none()

    # ── Security ─────────────────────────────────────────────────────────────

    def upsert_security(
        self,
        *,
        company_id: UUID,
        ticker: str,
        exchange: str | None,
        observed_at: datetime,
    ) -> tuple[Security, bool]:
        """Insert or update a security by (company_id, ticker, exchange).

        Returns (security, inserted).  The exchange column participates in the
        unique constraint so NULL exchange must match NULL exchange.
        """
        stmt = (
            pg_insert(Security)
            .values(
                company_id=company_id,
                ticker=ticker,
                exchange=exchange,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
                is_currently_reported_by_sec=True,
            )
            .on_conflict_do_update(
                index_elements=["company_id", "ticker", "exchange"],
                set_={
                    "last_observed_at": observed_at,
                    "is_currently_reported_by_sec": True,
                },
            )
            .returning(Security)
        )
        result = self._s.execute(stmt)
        row = result.fetchone()
        security = row[0]
        inserted = security.first_observed_at >= observed_at
        return security, inserted

    # ── SIC classification ───────────────────────────────────────────────────

    def upsert_sec_sic_classification(
        self,
        *,
        company_id: UUID,
        sic_code: str,
        sic_description: str | None,
        effective_from: date,
    ) -> bool:
        """Effective-date the SEC SIC classification for a company.

        If the active classification is identical to sic_code, does nothing.
        If changed: closes the old classification and inserts a new active one.

        Returns True if a new classification row was inserted.
        """
        active = self._s.execute(
            select(CompanyClassification).where(
                CompanyClassification.company_id == company_id,
                CompanyClassification.taxonomy == _SIC_TAXONOMY,
                CompanyClassification.classifier_version == _SIC_CLASSIFIER_VERSION,
                CompanyClassification.effective_to.is_(None),
            )
        ).scalar_one_or_none()

        if active is not None:
            if active.code == sic_code:
                return False  # unchanged
            # Close the old classification as of the effective_from date.
            active.effective_to = effective_from

        new_cls = CompanyClassification(
            company_id=company_id,
            taxonomy=_SIC_TAXONOMY,
            code=sic_code,
            label=sic_description or sic_code,
            source=_SIC_SOURCE,
            confidence=Decimal("1.0"),
            classifier_version=_SIC_CLASSIFIER_VERSION,
            effective_from=effective_from,
            effective_to=None,
            classification_metadata={},
        )
        self._s.add(new_cls)
        return True

    def mark_unobserved_securities_inactive(
        self,
        observed_security_ids: set[UUID],
        source_run_id: UUID,
    ) -> int:
        """Set is_currently_reported_by_sec=False for securities not seen in this run.

        Only call after a fully successful archive run.  Returns the count of
        securities marked inactive.

        This is a coarse operation; consider whether partial archive runs could
        incorrectly deactivate securities before calling this.
        """
        if not observed_security_ids:
            return 0
        result = self._s.execute(
            update(Security)
            .where(
                Security.security_id.not_in(observed_security_ids),
                Security.is_currently_reported_by_sec.is_(True),
            )
            .values(is_currently_reported_by_sec=False)
        )
        return result.rowcount
