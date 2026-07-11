"""CandidateUniverseService: orchestrates evaluation and persistence."""

from __future__ import annotations

import logging
from datetime import date

from dateutil.relativedelta import relativedelta

from invest_ml.universe.candidate import CandidateUniverseEvaluator
from invest_ml.universe.config import CandidateUniverseConfig
from invest_ml.universe.models import (
    CandidateCompanyInput,
    CandidateDecision,
    CandidateUniverseResult,
)

logger = logging.getLogger(__name__)


class CandidateUniverseService:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def materialize(
        self,
        *,
        as_of_date: date,
        universe_name: str,
        universe_version: str,
        profile_version: str,
        config: CandidateUniverseConfig,
    ) -> CandidateUniverseResult:
        from invest_ml.db.repositories.universe import UniverseRepository

        criteria_hash = config.criteria_hash()

        # ── 1. Create or validate universe definition ─────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            universe_def = repo.find_universe_definition(universe_name, universe_version)
            if universe_def is None:
                universe_def = repo.create_universe_definition(
                    name=universe_name,
                    version=universe_version,
                    purpose="candidate",
                    criteria={**config.to_criteria_dict(), "criteria_hash": criteria_hash},
                )
                session.commit()
            else:
                existing_hash = universe_def.criteria.get("criteria_hash")
                if existing_hash != criteria_hash:
                    raise ValueError(
                        f"Universe '{universe_name}/{universe_version}' already exists with "
                        f"criteria_hash={existing_hash!r} but current config yields "
                        f"{criteria_hash!r}. Bump universe_version to create a new definition."
                    )
            universe_id = universe_def.universe_id

        # ── 2. Load candidate inputs ──────────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            candidate_inputs = repo.list_candidate_inputs(
                profile_version=profile_version,
                exchange_aliases=config.exchange_aliases,
            )

        # ── 3. Evaluate all companies ─────────────────────────────────────────
        evaluator = CandidateUniverseEvaluator(config)
        decisions = [evaluator.evaluate(c, as_of_date=as_of_date) for c in candidate_inputs]

        # ── 4. Load current active memberships ────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            active = repo.list_active_memberships(universe_id)
        active_company_ids = {m.company_id for m in active}

        # ── 5. Compute changes ────────────────────────────────────────────────
        included_decisions = [d for d in decisions if d.included]
        included_company_ids = {d.company_id for d in included_decisions}

        newly_included = [
            d for d in included_decisions if d.company_id not in active_company_ids
        ]
        already_included = {
            d.company_id for d in included_decisions if d.company_id in active_company_ids
        }
        newly_excluded_ids = active_company_ids - included_company_ids

        decision_by_id = {d.company_id: d for d in decisions}
        company_by_id = {c.company_id: c for c in candidate_inputs}

        # ── 6. Persist changes atomically ─────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)

            for decision in newly_included:
                company_input = company_by_id[decision.company_id]
                repo.insert_membership(
                    universe_id=universe_id,
                    company_id=decision.company_id,
                    security_id=None,
                    included_from=as_of_date,
                    inclusion_reasons=_build_inclusion_reasons(
                        decision, company_input, config, as_of_date
                    ),
                )

            for company_id in newly_excluded_ids:
                decision = decision_by_id.get(company_id)
                repo.close_membership(
                    universe_id=universe_id,
                    company_id=company_id,
                    included_until=as_of_date,
                    exclusion_reasons=(
                        _build_exclusion_reasons(decision, as_of_date, config)
                        if decision
                        else {}
                    ),
                )

            session.commit()

        # ── 7. Compute exclusion stats ────────────────────────────────────────
        exclusion_counts: dict[str, int] = {}
        for d in decisions:
            if not d.included:
                for reason in d.exclusion_reasons:
                    exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

        logger.info(
            "candidate_universe: evaluated=%d included=%d newly_included=%d "
            "already_included=%d newly_excluded=%d universe_id=%s",
            len(decisions),
            len(included_decisions),
            len(newly_included),
            len(already_included),
            len(newly_excluded_ids),
            universe_id,
        )

        return CandidateUniverseResult(
            evaluated_companies=len(decisions),
            included_companies=len(included_decisions),
            newly_included=len(newly_included),
            already_included=len(already_included),
            newly_excluded=len(newly_excluded_ids),
            exclusion_counts=exclusion_counts,
            universe_id=universe_id,
            criteria_hash=criteria_hash,
        )


# ── Private helpers ──────────────────────────────────────────────────────────


def _build_inclusion_reasons(
    decision: CandidateDecision,
    company_input: CandidateCompanyInput,
    config: CandidateUniverseConfig,
    as_of_date: date,
) -> dict:
    cutoff = as_of_date - relativedelta(months=config.recent_filing_months)
    result: dict = {
        "entity_type": company_input.entity_type,
        "latest_filing_date": (
            company_input.latest_filing_date.isoformat()
            if company_input.latest_filing_date
            else None
        ),
        "filing_cutoff": cutoff.isoformat(),
        "eligible_securities": [
            {
                "ticker": s.ticker,
                "exchange": s.exchange,
                "normalized_exchange": s.normalized_exchange,
            }
            for s in decision.eligible_securities
        ],
        "sic_codes": list(company_input.sic_codes),
        "profile_version": config.profile_version,
    }
    if decision.inclusion_reasons:
        result["override"] = list(decision.inclusion_reasons)
        result["waived_exclusion_reasons"] = list(decision.exclusion_reasons)
    return result


def _build_exclusion_reasons(
    decision: CandidateDecision,
    as_of_date: date,
    config: CandidateUniverseConfig,
) -> dict:
    cutoff = as_of_date - relativedelta(months=config.recent_filing_months)
    return {
        "reason_codes": list(decision.exclusion_reasons),
        "required_cutoff": cutoff.isoformat(),
    }
