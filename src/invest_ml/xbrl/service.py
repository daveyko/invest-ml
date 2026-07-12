"""Orchestrates XBRL facts ingestion for all training-universe members.

Per-member transaction boundary:
  read → hash → get/create raw_version → claim derivation →
  flatten → bulk insert → mark succeeded → commit

Each member is isolated: a failure on one company does not affect others.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from invest_ml.sec.archive_cache import CachedArchive, SecBulkArchiveCache
from invest_ml.sec.companyfacts_flattener import CompanyFactsFlattener
from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader
from invest_ml.xbrl.models import (
    MemberIngestionResult,
    XbrlFactsIngestionResult,
    XbrlFactsIngestPlan,
)

logger = logging.getLogger(__name__)

_DERIVATION_TYPE = "xbrl_facts"


class XbrlFactsIngestionService:
    """Download (or reuse) the SEC companyfacts ZIP and flatten facts for each
    training-universe member into the xbrl_facts table."""

    def __init__(
        self,
        session_factory: sessionmaker,
        archive_cache: SecBulkArchiveCache,
        flattener: CompanyFactsFlattener,
        *,
        max_member_bytes: int = 50 * 1024 * 1024,
        force_refresh: bool = False,
        cache_only: bool = False,
        missing_threshold: float = 0.5,
        malformed_threshold: float = 0.5,
    ) -> None:
        self._sf = session_factory
        self._cache = archive_cache
        self._flattener = flattener
        self._max_bytes = max_member_bytes
        self._force_refresh = force_refresh
        self._cache_only = cache_only
        self._missing_threshold = missing_threshold
        self._malformed_threshold = malformed_threshold

    def build_plan(
        self,
        universe_name: str,
        universe_version: str,
    ) -> XbrlFactsIngestPlan:
        """Pre-flight: load members and identify which CIKs are in the archive."""
        from invest_ml.db.repositories.universe import UniverseRepository

        with self._sf() as session:
            repo = UniverseRepository(session)
            selected = repo.list_selected_companies(universe_name, universe_version)

        if not selected:
            raise ValueError(
                f"No active members in training universe {universe_name}/{universe_version}"
            )

        cached = self._cache.get_or_refresh(
            force_refresh=self._force_refresh,
            cache_only=self._cache_only,
        )

        reader = SelectedCompanyFactsReader(cached.path, self._max_bytes)
        target_ciks = frozenset(c.cik for c in selected)
        found_ciks = reader.list_found_ciks(target_ciks)

        return XbrlFactsIngestPlan(
            archive_path=cached.path,
            archive_sha256=cached.sha256,
            target_ciks=target_ciks,
            found_ciks=found_ciks,
            members_total=len(selected),
            derivation_type=_DERIVATION_TYPE,
            derivation_version=self._flattener.derivation_version,
        )

    def materialize(
        self,
        *,
        universe_name: str,
        universe_version: str,
        source_run_id: UUID,
        ingested_at: datetime,
    ) -> XbrlFactsIngestionResult:
        """Ingest XBRL facts for all training-universe members."""
        from invest_ml.db.repositories.universe import UniverseRepository

        with self._sf() as session:
            repo = UniverseRepository(session)
            selected = repo.list_selected_companies(universe_name, universe_version)

        if not selected:
            logger.warning(
                "No active members in training universe %s/%s; nothing to ingest",
                universe_name, universe_version,
            )
            return XbrlFactsIngestionResult(
                members_processed=0,
                members_succeeded=0,
                members_failed=0,
                members_skipped_not_found=0,
                members_skipped_already_done=0,
                total_facts_inserted=0,
                total_facts_already_existed=0,
                derivation_version=self._flattener.derivation_version,
            )

        cached = self._cache.get_or_refresh(
            force_refresh=self._force_refresh,
            cache_only=self._cache_only,
        )
        reader = SelectedCompanyFactsReader(cached.path, self._max_bytes)
        target_ciks = frozenset(c.cik for c in selected)
        found_ciks = reader.list_found_ciks(target_ciks)
        missing_ciks = target_ciks - found_ciks

        missing_ratio = len(missing_ciks) / max(len(target_ciks), 1)
        if missing_ratio > self._missing_threshold:
            raise RuntimeError(
                f"Too many missing CIKs in archive: "
                f"{len(missing_ciks)}/{len(target_ciks)} ({missing_ratio:.0%}) "
                f"> threshold {self._missing_threshold:.0%}"
            )

        if missing_ciks:
            logger.warning(
                "%d/%d target CIKs not found in archive",
                len(missing_ciks), len(target_ciks),
            )

        companies_by_cik = {c.cik: c for c in selected}
        member_results: list[MemberIngestionResult] = []

        for cik in sorted(found_ciks):
            company = companies_by_cik[cik]
            result = self._process_member(
                reader=reader,
                company_cik=cik,
                company_id=company.company_id,
                cached_archive=cached,
                source_run_id=source_run_id,
                ingested_at=ingested_at,
            )
            member_results.append(result)

        for cik in sorted(missing_ciks):
            member_results.append(MemberIngestionResult(
                cik=cik,
                succeeded=False,
                facts_inserted=0,
                facts_already_existed=0,
                skipped_reason="not_found_in_archive",
                error=None,
            ))

        failed = [r for r in member_results if not r.succeeded and r.skipped_reason is None]
        malformed_ratio = len(failed) / max(len(member_results), 1)
        if malformed_ratio > self._malformed_threshold:
            raise RuntimeError(
                f"Too many member failures: {len(failed)}/{len(member_results)} "
                f"({malformed_ratio:.0%}) > threshold {self._malformed_threshold:.0%}"
            )

        return XbrlFactsIngestionResult(
            members_processed=len(found_ciks),
            members_succeeded=sum(1 for r in member_results if r.succeeded),
            members_failed=len(failed),
            members_skipped_not_found=len(missing_ciks),
            members_skipped_already_done=sum(
                1 for r in member_results if r.skipped_reason == "already_succeeded"
            ),
            total_facts_inserted=sum(r.facts_inserted for r in member_results),
            total_facts_already_existed=sum(r.facts_already_existed for r in member_results),
            derivation_version=self._flattener.derivation_version,
        )

    def _process_member(
        self,
        *,
        reader: SelectedCompanyFactsReader,
        company_cik: str,
        company_id: UUID,
        cached_archive: CachedArchive,
        source_run_id: UUID,
        ingested_at: datetime,
    ) -> MemberIngestionResult:
        from invest_ml.db.repositories.xbrl_facts import XbrlFactsRepository

        try:
            payload = reader.read_member(company_cik)
        except Exception as exc:
            logger.error("Failed to read archive member CIK%s: %s", company_cik, exc)
            return MemberIngestionResult(
                cik=company_cik,
                succeeded=False,
                facts_inserted=0,
                facts_already_existed=0,
                skipped_reason=None,
                error=str(exc)[:2000],
            )

        if payload is None:
            return MemberIngestionResult(
                cik=company_cik,
                succeeded=False,
                facts_inserted=0,
                facts_already_existed=0,
                skipped_reason="not_found_in_archive",
                error=None,
            )

        content_hash = hashlib.sha256(payload).hexdigest()
        entity_key = f"CIK{company_cik}"
        object_uri = f"file://{cached_archive.path}"
        source_locator = {
            "type": "sec_companyfacts_zip",
            "archive_sha256": cached_archive.sha256,
            "member_cik": company_cik,
        }

        with self._sf() as session:
            xbrl_repo = XbrlFactsRepository(session)

            raw_version_id, _ = xbrl_repo.get_or_create_member_version(
                source="sec_companyfacts",
                entity_key=entity_key,
                content_hash=content_hash,
                source_locator=source_locator,
                object_uri=object_uri,
                source_run_id=source_run_id,
                observed_at=ingested_at,
                byte_size=len(payload),
            )

            claimed = xbrl_repo.claim_derivation(
                raw_version_id=raw_version_id,
                derivation_type=_DERIVATION_TYPE,
                derivation_version=self._flattener.derivation_version,
                started_at=ingested_at,
            )

            if not claimed:
                session.commit()
                return MemberIngestionResult(
                    cik=company_cik,
                    succeeded=True,
                    facts_inserted=0,
                    facts_already_existed=0,
                    skipped_reason="already_succeeded",
                    error=None,
                )

            try:
                facts = self._flattener.flatten(company_id, raw_version_id, payload)
                insert_result = xbrl_repo.bulk_insert_facts(facts, ingested_at=ingested_at)
                xbrl_repo.mark_derivation_succeeded(
                    raw_version_id,
                    _DERIVATION_TYPE,
                    self._flattener.derivation_version,
                    row_count=insert_result.inserted,
                )
                session.commit()
                logger.debug(
                    "CIK%s: inserted=%d already_existed=%d",
                    company_cik, insert_result.inserted, insert_result.already_existed,
                )
                return MemberIngestionResult(
                    cik=company_cik,
                    succeeded=True,
                    facts_inserted=insert_result.inserted,
                    facts_already_existed=insert_result.already_existed,
                    skipped_reason=None,
                    error=None,
                )

            except Exception as exc:
                error_msg = str(exc)[:2000]
                logger.error("Failed to process CIK%s: %s", company_cik, exc)
                try:
                    xbrl_repo.mark_derivation_failed(
                        raw_version_id,
                        _DERIVATION_TYPE,
                        self._flattener.derivation_version,
                        error=error_msg,
                    )
                    session.commit()
                except Exception:
                    session.rollback()

                return MemberIngestionResult(
                    cik=company_cik,
                    succeeded=False,
                    facts_inserted=0,
                    facts_already_existed=0,
                    skipped_reason=None,
                    error=error_msg,
                )
