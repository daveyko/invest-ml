"""CompanyCatalogService: parse the submissions archive and persist the catalog.

Domain logic lives here; the Dagster asset is a thin orchestration wrapper.

Key invariants:
- A single malformed filer record must not abort the entire archive run.
- Database failures (e.g. constraint violations) must fail the batch and propagate.
- Re-running the same archive is idempotent.
- The full archive is never held in memory; processing is streaming and batched.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from invest_ml.company_catalog.models import CompanyCatalogResult
from invest_ml.db.repositories.company_catalog import CompanyCatalogRepository
from invest_ml.sec.archive import SubmissionArchiveReader
from invest_ml.sec.submissions import SubmissionCompanyParser

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


class CompanyCatalogService:
    """Orchestrates the archive → parse → upsert pipeline for one archive run."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        archive_reader: SubmissionArchiveReader | None = None,
        parser: SubmissionCompanyParser | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._reader = archive_reader or SubmissionArchiveReader()
        self._parser = parser or SubmissionCompanyParser()

    def refresh_catalog(
        self,
        archive_path: Path,
        run_id: UUID,
        observed_at: datetime,
    ) -> CompanyCatalogResult:
        """Stream and persist all primary filer records from the archive.

        Processes filers in batches of _BATCH_SIZE, committing each batch
        independently.  A malformed JSON record is counted but does not roll
        back valid records in its batch.

        Parameters
        ----------
        archive_path:
            Local path to the downloaded submissions ZIP.
        run_id:
            UUID of the active IngestionRun; used as source_run_id on Company rows.
        observed_at:
            Timestamp to stamp first_observed_at / last_observed_at.  All rows
            within one run share the same timestamp for consistency.
        """
        result = CompanyCatalogResult()
        effective_date = observed_at.date()

        batch: list[_RecordTuple] = []

        for record in self._reader.iter_company_records(archive_path):
            parse_result = self._parser.parse(record.payload, record.member_name)

            result.parse_warnings.extend(parse_result.warnings)

            if not parse_result.ok:
                logger.warning(
                    "Malformed record %s: %s", record.member_name, parse_result.error
                )
                result.malformed_records += 1
                continue

            batch.append(_RecordTuple(member_name=record.member_name, company=parse_result.company))

            if len(batch) >= _BATCH_SIZE:
                self._flush_batch(batch, run_id, observed_at, effective_date, result)
                batch.clear()

        if batch:
            self._flush_batch(batch, run_id, observed_at, effective_date, result)

        return result

    # ── Private ──────────────────────────────────────────────────────────────

    def _flush_batch(
        self,
        batch: list[_RecordTuple],
        run_id: UUID,
        observed_at: datetime,
        effective_date,
        result: CompanyCatalogResult,
    ) -> None:
        with self._session_factory() as session:
            repo = CompanyCatalogRepository(session)
            for item in batch:
                cat_company = item.company
                try:
                    company, inserted = repo.upsert_company(
                        cik=cat_company.cik,
                        legal_name=cat_company.legal_name,
                        entity_type=cat_company.entity_type,
                        filer_category=cat_company.filer_category,
                        fiscal_year_end=cat_company.fiscal_year_end,
                        state_of_incorporation=cat_company.state_of_incorporation,
                        latest_filing_date=cat_company.latest_filing_date,
                        observed_at=observed_at,
                        source_run_id=run_id,
                    )
                    result.companies_seen += 1
                    if inserted:
                        result.companies_inserted += 1
                    else:
                        result.companies_updated += 1

                    # Upsert securities.
                    for sec in cat_company.securities:
                        _, sec_inserted = repo.upsert_security(
                            company_id=company.company_id,
                            ticker=sec.ticker,
                            exchange=sec.exchange,
                            observed_at=observed_at,
                        )
                        if sec_inserted:
                            result.securities_inserted += 1
                        else:
                            result.securities_updated += 1

                    # Upsert SIC classification if present.
                    if cat_company.sic:
                        new_cls = repo.upsert_sec_sic_classification(
                            company_id=company.company_id,
                            sic_code=cat_company.sic,
                            sic_description=cat_company.sic_description,
                            effective_from=effective_date,
                        )
                        if new_cls:
                            result.sic_classifications_inserted += 1

                except Exception:
                    logger.exception(
                        "DB error persisting CIK %s (member=%s); "
                        "rolling back batch and skipping this record.",
                        cat_company.cik,
                        item.member_name,
                    )
                    # Re-raise; the caller marks the ingestion run as failed.
                    raise

            session.commit()


class _RecordTuple:
    __slots__ = ("member_name", "company")

    def __init__(self, member_name: str, company) -> None:  # type: ignore[annotation-unchecked]
        self.member_name = member_name
        self.company = company
