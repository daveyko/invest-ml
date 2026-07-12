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

    # ── Training universe inputs ──────────────────────────────────────────────

    def load_training_company_inputs(
        self,
        candidate_universe_name: str,
        candidate_universe_version: str,
        company_data_profile_version: str,
        market_profile_version: str,
    ) -> list:
        """Load all candidate members with securities, data profiles, and market profiles.

        Uses 6 targeted queries and Python dicts — no N+1, no Cartesian product.
        """
        from collections import defaultdict
        from decimal import Decimal

        from invest_ml.db.models.company import Company, Security
        from invest_ml.db.models.profiling import CompanyDataProfile, CompanyMarketProfile
        from invest_ml.universe.training import TrainingCompanyInput

        # 1. Get active candidate universe definition
        cand_def = self._s.execute(
            select(UniverseDefinition).where(
                UniverseDefinition.name == candidate_universe_name,
                UniverseDefinition.version == candidate_universe_version,
            )
        ).scalar_one_or_none()
        if cand_def is None:
            return []

        # 2. Get company IDs with active candidate membership
        candidate_company_ids: list = list(
            self._s.execute(
                select(UniverseMembership.company_id).where(
                    UniverseMembership.universe_id == cand_def.universe_id,
                    UniverseMembership.included_until.is_(None),
                )
            )
            .scalars()
            .all()
        )
        if not candidate_company_ids:
            return []

        # 3. Load company rows
        companies_by_id = {
            c.company_id: c
            for c in self._s.execute(
                select(Company).where(Company.company_id.in_(candidate_company_ids))
            )
            .scalars()
            .all()
        }

        # 4. Load company data profiles for the configured version
        profiles_by_company = {
            p.company_id: p
            for p in self._s.execute(
                select(CompanyDataProfile).where(
                    CompanyDataProfile.company_id.in_(candidate_company_ids),
                    CompanyDataProfile.profile_version == company_data_profile_version,
                )
            )
            .scalars()
            .all()
        }

        # 5. Load currently-observed securities
        securities_by_company: dict = defaultdict(list)
        all_securities = self._s.execute(
            select(Security).where(
                Security.company_id.in_(candidate_company_ids),
                Security.is_currently_reported_by_sec.is_(True),
            )
        ).scalars().all()
        for s in all_securities:
            securities_by_company[s.company_id].append(s)

        # 6. Load market profiles for all relevant securities
        all_security_ids = [s.security_id for s in all_securities]
        market_profiles_by_security = {}
        if all_security_ids:
            for mp in self._s.execute(
                select(CompanyMarketProfile).where(
                    CompanyMarketProfile.security_id.in_(all_security_ids),
                    CompanyMarketProfile.profile_version == market_profile_version,
                )
            ).scalars().all():
                market_profiles_by_security[mp.security_id] = mp

        # 7. Assemble TrainingCompanyInput objects
        result = []
        for company_id in candidate_company_ids:
            company = companies_by_id.get(company_id)
            if company is None:
                continue
            profile = profiles_by_company.get(company_id)
            raw_securities = securities_by_company.get(company_id, [])

            eligible_securities = tuple(
                self._build_eligible_security_input(
                    s, market_profiles_by_security.get(s.security_id)
                )
                for s in raw_securities
            )

            result.append(
                TrainingCompanyInput(
                    company_id=company_id,
                    cik=company.cik,
                    legal_name=company.legal_name,
                    candidate_membership_active=True,
                    company_data_profile_version=(
                        profile.profile_version if profile else None
                    ),
                    annual_periods=profile.annual_periods if profile else 0,
                    quarterly_periods=profile.quarterly_periods if profile else 0,
                    canonical_metric_coverage=Decimal(
                        str(profile.canonical_metric_coverage)
                    ) if profile and profile.canonical_metric_coverage is not None
                    else Decimal("0"),
                    company_data_quality_flags=profile.quality_flags if profile else {},
                    securities=eligible_securities,
                )
            )
        return result

    def load_scoring_company_inputs(
        self,
        training_universe_name: str,
        training_universe_version: str,
    ) -> list:
        """Load training-universe members with SIC codes for scoring evaluation."""
        from collections import defaultdict

        from invest_ml.db.models.classification import CompanyClassification
        from invest_ml.db.models.company import Company, Security
        from invest_ml.universe.scoring import ScoringCompanyInput

        # 1. Get training universe definition
        training_def = self._s.execute(
            select(UniverseDefinition).where(
                UniverseDefinition.name == training_universe_name,
                UniverseDefinition.version == training_universe_version,
            )
        ).scalar_one_or_none()
        if training_def is None:
            return []

        # 2. Load active training memberships (these carry security_id)
        active_memberships = self._s.execute(
            select(UniverseMembership).where(
                UniverseMembership.universe_id == training_def.universe_id,
                UniverseMembership.included_until.is_(None),
            )
        ).scalars().all()
        if not active_memberships:
            return []

        company_ids = [m.company_id for m in active_memberships]
        membership_by_company = {m.company_id: m for m in active_memberships}

        # 3. Load company rows
        companies_by_id = {
            c.company_id: c
            for c in self._s.execute(
                select(Company).where(Company.company_id.in_(company_ids))
            ).scalars().all()
        }

        # 4. Load security rows (to get ticker for the selected security)
        security_ids = [m.security_id for m in active_memberships if m.security_id]
        securities_by_id = {}
        if security_ids:
            for s in self._s.execute(
                select(Security).where(Security.security_id.in_(security_ids))
            ).scalars().all():
                securities_by_id[s.security_id] = s

        # 5. Load active SIC codes
        sics_by_company: dict = defaultdict(list)
        for cls_row in self._s.execute(
            select(CompanyClassification).where(
                CompanyClassification.company_id.in_(company_ids),
                CompanyClassification.taxonomy == "sec_sic",
                CompanyClassification.effective_to.is_(None),
            )
        ).scalars().all():
            sics_by_company[cls_row.company_id].append(cls_row.code)

        # 6. Assemble ScoringCompanyInput objects
        result = []
        for company_id in company_ids:
            company = companies_by_id.get(company_id)
            membership = membership_by_company[company_id]
            security = securities_by_id.get(membership.security_id) if membership.security_id else None
            if company is None or security is None:
                continue

            result.append(
                ScoringCompanyInput(
                    company_id=company_id,
                    security_id=membership.security_id,
                    cik=company.cik,
                    ticker=security.ticker,
                    legal_name=company.legal_name,
                    active_sic_codes=tuple(sorted(sics_by_company.get(company_id, []))),
                    training_inclusion_reasons=membership.inclusion_reasons or {},
                )
            )
        return result

    def _build_eligible_security_input(self, security, market_profile):
        from decimal import Decimal

        from invest_ml.universe.security_selector import EligibleSecurityInput

        status = None
        if market_profile and isinstance(market_profile.quality_flags, dict):
            status = market_profile.quality_flags.get("status")

        def _dec(v):
            return Decimal(str(v)) if v is not None else None

        return EligibleSecurityInput(
            security_id=security.security_id,
            company_id=security.company_id,
            ticker=security.ticker,
            exchange=security.exchange,
            currently_observed=security.is_currently_reported_by_sec,
            market_profile_version=(
                market_profile.profile_version if market_profile else None
            ),
            market_profile_scanned_at=(
                market_profile.scanned_at if market_profile else None
            ),
            market_profile_status=status,
            first_price_date=market_profile.first_price_date if market_profile else None,
            latest_price_date=market_profile.latest_price_date if market_profile else None,
            price_history_years=_dec(market_profile.price_history_years) if market_profile else None,
            median_daily_dollar_volume=_dec(market_profile.median_daily_dollar_volume) if market_profile else None,
            current_market_cap=_dec(market_profile.current_market_cap) if market_profile else None,
            missing_trading_day_ratio=_dec(market_profile.missing_trading_day_ratio) if market_profile else None,
            latest_adjusted_close=_dec(market_profile.latest_adjusted_close) if market_profile else None,
        )

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
