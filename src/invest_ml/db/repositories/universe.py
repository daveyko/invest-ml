"""Repository for universe definitions, memberships, and candidate inputs."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from invest_ml.db.models.ingestion import IngestionRun
from invest_ml.db.models.universe import UniverseDefinition, UniverseMembership

logger = logging.getLogger(__name__)


class UniverseRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Universe definition ───────────────────────────────────────────────────

    def find_universe_definition(self, name: str, version: str) -> UniverseDefinition | None:
        return self._s.execute(
            select(UniverseDefinition).where(
                UniverseDefinition.name == name,
                UniverseDefinition.version == version,
            )
        ).scalar_one_or_none()

    def create_universe_definition(
        self, name: str, version: str, purpose: str, criteria: dict
    ) -> UniverseDefinition:
        defn = UniverseDefinition(
            name=name,
            version=version,
            purpose=purpose,
            criteria=criteria,
            created_at=datetime.now(tz=UTC),
        )
        self._s.add(defn)
        self._s.flush()
        return defn

    # ── Memberships ───────────────────────────────────────────────────────────

    def list_active_memberships(self, universe_id: UUID) -> list[UniverseMembership]:
        return list(
            self._s.execute(
                select(UniverseMembership).where(
                    UniverseMembership.universe_id == universe_id,
                    UniverseMembership.included_until.is_(None),
                )
            )
            .scalars()
            .all()
        )

    def insert_membership(
        self,
        *,
        universe_id: UUID,
        company_id: UUID,
        security_id: UUID | None,
        included_from,
        inclusion_reasons: dict,
    ) -> None:
        stmt = (
            pg_insert(UniverseMembership)
            .values(
                universe_id=universe_id,
                company_id=company_id,
                security_id=security_id,
                included_from=included_from,
                included_until=None,
                inclusion_reasons=inclusion_reasons,
                exclusion_reasons=None,
            )
            .on_conflict_do_nothing()
        )
        self._s.execute(stmt)

    def close_membership(
        self,
        *,
        universe_id: UUID,
        company_id: UUID,
        included_until,
        exclusion_reasons: dict,
    ) -> None:
        self._s.execute(
            update(UniverseMembership)
            .where(
                UniverseMembership.universe_id == universe_id,
                UniverseMembership.company_id == company_id,
                UniverseMembership.included_until.is_(None),
            )
            .values(included_until=included_until, exclusion_reasons=exclusion_reasons)
        )

    # ── Candidate inputs ──────────────────────────────────────────────────────

    def list_candidate_inputs(
        self, profile_version: str, exchange_aliases: dict[str, str]
    ) -> list:
        """Load all companies with their securities, SIC codes, and profile status.

        Uses 4 focused queries rather than a wide join to avoid Cartesian products
        when a company has multiple securities or multiple SIC classifications.
        """
        from invest_ml.db.models.classification import CompanyClassification
        from invest_ml.db.models.company import Company, Security
        from invest_ml.db.models.profiling import CompanyDataProfile
        from invest_ml.universe.models import CandidateCompanyInput, CandidateSecurity

        companies = self._s.execute(select(Company)).scalars().all()
        if not companies:
            return []

        securities_rows = self._s.execute(
            select(Security).where(Security.is_currently_reported_by_sec.is_(True))
        ).scalars().all()
        secs_by_company: dict[UUID, list] = defaultdict(list)
        for s in securities_rows:
            secs_by_company[s.company_id].append(s)

        sics_rows = self._s.execute(
            select(CompanyClassification).where(
                CompanyClassification.taxonomy == "sec_sic",
                CompanyClassification.effective_to.is_(None),
            )
        ).scalars().all()
        sics_by_company: dict[UUID, set] = defaultdict(set)
        for sic in sics_rows:
            sics_by_company[sic.company_id].add(sic.code)

        profiled_ids: set[UUID] = set(
            self._s.execute(
                select(CompanyDataProfile.company_id).where(
                    CompanyDataProfile.profile_version == profile_version
                )
            )
            .scalars()
            .all()
        )

        result = []
        for company in companies:
            raw_secs = secs_by_company.get(company.company_id, [])
            candidate_secs = tuple(
                CandidateSecurity(
                    security_id=s.security_id,
                    ticker=s.ticker,
                    exchange=s.exchange,
                    normalized_exchange=exchange_aliases.get((s.exchange or "").strip()),
                    currently_observed=s.is_currently_reported_by_sec,
                )
                for s in raw_secs
            )
            sic_codes = tuple(sorted(sics_by_company.get(company.company_id, set())))
            result.append(
                CandidateCompanyInput(
                    company_id=company.company_id,
                    cik=company.cik,
                    legal_name=company.legal_name,
                    entity_type=company.entity_type,
                    latest_filing_date=company.latest_filing_date,
                    sic_codes=sic_codes,
                    has_current_data_profile=company.company_id in profiled_ids,
                    securities=candidate_secs,
                )
            )
        return result

    # ── Ingestion run ─────────────────────────────────────────────────────────

    def create_ingestion_run(
        self, source: str, source_uri: str, started_at: datetime
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
