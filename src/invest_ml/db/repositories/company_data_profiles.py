"""Repository for company data profiles.

Provides idempotent upsert for CompanyDataProfile rows and a query to
identify which companies should be targeted by the companyfacts scan.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from invest_ml.db.models.company import Company, Security
from invest_ml.db.models.ingestion import IngestionRun
from invest_ml.db.models.profiling import CompanyDataProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompanyProfileTarget:
    company_id: UUID
    cik: str


@dataclass
class ProfileUpsertResult:
    upserted: int = 0


class CompanyDataProfileRepository:
    """Provides ingestion-run management and profile upserts for the companyfacts scan."""

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
                error=error[:2000],
            )
        )

    def find_latest_successful_ingestion_run(self, source: str) -> IngestionRun | None:
        return self._s.execute(
            select(IngestionRun)
            .where(IngestionRun.source == source, IngestionRun.status == "succeeded")
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    # ── Profiling targets ─────────────────────────────────────────────────────

    def list_companyfacts_profile_targets(
        self,
        *,
        exchanges: Collection[str],
        entity_types: Collection[str],
    ) -> list[CompanyProfileTarget]:
        """Return all (company_id, cik) pairs eligible for companyfacts profiling.

        Filters to companies that:
        - have at least one security on an accepted exchange currently reported by SEC
        - have entity_type in entity_types, OR entity_type is NULL (not yet classified)
        """
        stmt = (
            select(Company.company_id, Company.cik)
            .join(Security, Security.company_id == Company.company_id)
            .where(
                Security.is_currently_reported_by_sec.is_(True),
                Security.exchange.in_(list(exchanges)),
                or_(
                    Company.entity_type.is_(None),
                    Company.entity_type.in_(list(entity_types)),
                ),
            )
            .distinct()
        )
        rows = self._s.execute(stmt).all()
        return [CompanyProfileTarget(company_id=r[0], cik=r[1]) for r in rows]

    # ── Profile upserts ───────────────────────────────────────────────────────

    def upsert_profiles(
        self,
        profiles: Sequence,
    ) -> ProfileUpsertResult:
        """Idempotently insert or update CompanyDataProfile rows.

        Uses INSERT … ON CONFLICT DO UPDATE on the composite PK
        (company_id, profile_version).  Caller is responsible for committing.
        """
        count = 0
        for profile in profiles:
            values = _profile_to_dict(profile)
            stmt = (
                pg_insert(CompanyDataProfile)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["company_id", "profile_version"],
                    set_={
                        k: v
                        for k, v in values.items()
                        if k not in ("company_id", "profile_version")
                    },
                )
            )
            self._s.execute(stmt)
            count += 1
        return ProfileUpsertResult(upserted=count)


def _profile_to_dict(p: object) -> dict:
    """Convert a CompanyDataProfileResult to a plain dict for SQLAlchemy insert."""
    return {
        "company_id": p.company_id,
        "profile_version": p.profile_version,
        "scanned_at": p.scanned_at,
        "source_run_id": p.source_run_id,
        "first_period_end": p.first_period_end,
        "latest_period_end": p.latest_period_end,
        "latest_filed_date": p.latest_filed_date,
        "annual_periods": p.annual_periods,
        "quarterly_periods": p.quarterly_periods,
        "has_revenue": p.has_revenue,
        "has_operating_income": p.has_operating_income,
        "has_net_income": p.has_net_income,
        "has_operating_cash_flow": p.has_operating_cash_flow,
        "has_cash": p.has_cash,
        "has_debt": p.has_debt,
        "has_shares": p.has_shares,
        "canonical_metric_coverage": p.canonical_metric_coverage,
        "fact_count": p.fact_count,
        "quality_flags": p.quality_flags,
    }
