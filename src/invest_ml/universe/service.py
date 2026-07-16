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


# ── Training universe service ────────────────────────────────────────────────


class TrainingUniverseService:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def materialize(
        self,
        *,
        as_of_date: date,
        config,
    ):
        from invest_ml.db.repositories.universe import UniverseRepository
        from invest_ml.universe.training import TrainingUniverseEvaluator, TrainingUniverseResult

        criteria_hash = config.criteria_hash()
        criteria_dict = {**config.to_criteria_dict(), "criteria_hash": criteria_hash}

        # ── 1. Create or validate universe definition ─────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            universe_def = repo.find_universe_definition(config.name, config.version)
            if universe_def is None:
                universe_def = repo.create_universe_definition(
                    name=config.name,
                    version=config.version,
                    purpose="training",
                    criteria=criteria_dict,
                )
                session.commit()
            else:
                existing_hash = universe_def.criteria.get("criteria_hash")
                if existing_hash != criteria_hash:
                    raise ValueError(
                        f"Universe '{config.name}/{config.version}' already exists with "
                        f"criteria_hash={existing_hash!r} but current config yields "
                        f"{criteria_hash!r}. Bump version to create a new definition."
                    )
            universe_id = universe_def.universe_id

        # ── 2. Load company inputs ────────────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            companies = repo.load_training_company_inputs(
                candidate_universe_name=config.candidate_universe_name,
                candidate_universe_version=config.candidate_universe_version,
                company_data_profile_version=config.company_data_profile_version,
                market_profile_version=config.market_profile_version,
            )

        # ── 3. Evaluate ───────────────────────────────────────────────────────
        evaluator = TrainingUniverseEvaluator()
        decisions = [evaluator.evaluate(c, as_of_date=as_of_date, config=config) for c in companies]

        # ── 4. Load active memberships ────────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            active_memberships = repo.list_active_memberships(universe_id)

        active_by_company = {m.company_id: m for m in active_memberships}
        decision_by_id = {d.company_id: d for d in decisions}

        included_decisions = [d for d in decisions if d.included]
        included_by_company = {d.company_id: d for d in included_decisions}

        # ── 5. Compute diffs ──────────────────────────────────────────────────
        newly_included = []
        already_included_count = 0
        security_changes = []
        newly_excluded = []

        for decision in included_decisions:
            existing = active_by_company.get(decision.company_id)
            if existing is None:
                newly_included.append(decision)
            elif existing.security_id != decision.selected_security.security_id:
                security_changes.append((existing, decision))
            else:
                already_included_count += 1

        for company_id, existing in active_by_company.items():
            if company_id not in included_by_company:
                newly_excluded.append((existing, decision_by_id.get(company_id)))

        # ── 6. Persist atomically ─────────────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)

            for decision in newly_included:
                repo.insert_membership(
                    universe_id=universe_id,
                    company_id=decision.company_id,
                    security_id=decision.selected_security.security_id,
                    included_from=as_of_date,
                    inclusion_reasons=decision.inclusion_reasons,
                )

            for existing, decision in security_changes:
                prev_ticker = (
                    (existing.inclusion_reasons or {})
                    .get("selected_security", {})
                    .get("ticker")
                )
                repo.close_membership(
                    universe_id=universe_id,
                    company_id=decision.company_id,
                    included_until=as_of_date,
                    exclusion_reasons={
                        "reason_codes": ["selected_security_changed"],
                        "details": {
                            "previous_security_id": str(existing.security_id),
                            "previous_ticker": prev_ticker,
                            "new_security_id": str(decision.selected_security.security_id),
                            "new_ticker": decision.selected_security.ticker,
                        },
                    },
                )
                repo.insert_membership(
                    universe_id=universe_id,
                    company_id=decision.company_id,
                    security_id=decision.selected_security.security_id,
                    included_from=as_of_date,
                    inclusion_reasons=decision.inclusion_reasons,
                )

            for existing, decision in newly_excluded:
                repo.close_membership(
                    universe_id=universe_id,
                    company_id=existing.company_id,
                    included_until=as_of_date,
                    exclusion_reasons=decision.exclusion_reasons if decision else {},
                )

            session.commit()

        # ── 7. Stats ──────────────────────────────────────────────────────────
        exclusion_counts: dict[str, int] = {}
        for d in decisions:
            if not d.included:
                for code in d.exclusion_reasons.get("reason_codes", []):
                    exclusion_counts[code] = exclusion_counts.get(code, 0) + 1

        logger.info(
            "training_universe: evaluated=%d included=%d newly_included=%d "
            "security_changes=%d newly_excluded=%d universe_id=%s",
            len(decisions),
            len(included_decisions),
            len(newly_included),
            len(security_changes),
            len(newly_excluded),
            universe_id,
        )

        return TrainingUniverseResult(
            evaluated_companies=len(decisions),
            included_companies=len(included_decisions),
            newly_included=len(newly_included),
            already_included=already_included_count,
            newly_excluded=len(newly_excluded),
            selected_security_changes=len(security_changes),
            exclusion_counts=exclusion_counts,
            universe_id=universe_id,
            criteria_hash=criteria_hash,
        )


# ── Monthly-partitioned training universe service ────────────────────────────


class TrainingUniversePartitionService:
    """Materialize one monthly partition of the point-in-time training universe.

    Each call creates exactly one universe_definitions row keyed by
    (name, version, as_of_date) and inserts membership rows for all companies
    that passed eligibility as of that date.

    The operation is idempotent: if the (name, version, as_of_date) row already
    exists the method returns immediately with the current member count.
    """

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def materialize(
        self,
        *,
        as_of_date: date,
        config,
        total_canonical_metrics: int,
    ):
        from invest_ml.db.repositories.universe import UniverseRepository
        from invest_ml.universe.training import (
            TrainingUniverseEvaluator,
            TrainingUniversePartitionResult,
        )

        criteria_hash = config.criteria_hash()

        # ── 1. Idempotency check ──────────────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            existing = repo.find_universe_definition(config.name, config.version, as_of_date)
            if existing is not None:
                membership_count = repo.count_active_memberships(existing.universe_id)
                return TrainingUniversePartitionResult(
                    as_of_date=as_of_date,
                    evaluated_companies=0,
                    included_companies=membership_count,
                    newly_included=0,
                    already_present=True,
                    criteria_hash=criteria_hash,
                    universe_id=existing.universe_id,
                )

        # ── 2. Load point-in-time candidate inputs ────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            companies = repo.load_training_company_inputs_point_in_time(
                candidate_universe_name=config.candidate_universe_name,
                candidate_universe_version=config.candidate_universe_version,
                as_of_date=as_of_date,
                normalization_version=config.normalization_version,
                market_profile_version=config.market_profile_version,
                total_canonical_metrics=total_canonical_metrics,
                liquidity_lookback_sessions=config.liquidity_lookback_sessions,
                missing_ratio_lookback_years=config.missing_ratio_lookback_years,
            )

        # ── 3. Evaluate ───────────────────────────────────────────────────────
        eligibility_config = config.to_training_universe_config()
        evaluator = TrainingUniverseEvaluator()
        decisions = [
            evaluator.evaluate(c, as_of_date=as_of_date, config=eligibility_config)
            for c in companies
        ]
        included = [d for d in decisions if d.included]

        criteria_dict = {
            **config.to_criteria_dict(),
            "criteria_hash": criteria_hash,
            "as_of_date": as_of_date.isoformat(),
        }

        # ── 4. Persist atomically (definition + memberships) ──────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            universe_def = repo.create_universe_definition(
                name=config.name,
                version=config.version,
                purpose="training",
                criteria=criteria_dict,
                as_of_date=as_of_date,
            )

            for decision in included:
                repo.insert_membership(
                    universe_id=universe_def.universe_id,
                    company_id=decision.company_id,
                    security_id=decision.selected_security.security_id,
                    included_from=as_of_date,
                    inclusion_reasons=decision.inclusion_reasons,
                )

            session.commit()

        # ── 5. Stats ──────────────────────────────────────────────────────────
        exclusion_counts: dict[str, int] = {}
        for d in decisions:
            if not d.included:
                for code in d.exclusion_reasons.get("reason_codes", []):
                    exclusion_counts[code] = exclusion_counts.get(code, 0) + 1

        logger.info(
            "training_universe_partition: as_of_date=%s evaluated=%d included=%d "
            "universe_id=%s",
            as_of_date,
            len(decisions),
            len(included),
            universe_def.universe_id,
        )

        return TrainingUniversePartitionResult(
            as_of_date=as_of_date,
            evaluated_companies=len(decisions),
            included_companies=len(included),
            newly_included=len(included),
            already_present=False,
            criteria_hash=criteria_hash,
            universe_id=universe_def.universe_id,
            exclusion_counts=exclusion_counts,
        )


# ── Scoring universe service ─────────────────────────────────────────────────


class ScoringUniverseService:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def materialize(
        self,
        *,
        as_of_date: date,
        config,
        sic_buckets,
    ):
        from invest_ml.db.repositories.universe import UniverseRepository
        from invest_ml.universe.scoring import ScoringUniverseEvaluator, ScoringUniverseResult

        sic_bucket_hash = sic_buckets.config_hash()
        criteria_hash = config.criteria_hash(sic_bucket_hash)
        criteria_dict = config.to_criteria_dict(sic_bucket_hash)

        # ── 1. Create or validate universe definition ─────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            universe_def = repo.find_universe_definition(config.name, config.version)
            if universe_def is None:
                universe_def = repo.create_universe_definition(
                    name=config.name,
                    version=config.version,
                    purpose="scoring",
                    criteria=criteria_dict,
                )
                session.commit()
            else:
                existing_hash = universe_def.criteria.get("criteria_hash")
                if existing_hash != criteria_hash:
                    raise ValueError(
                        f"Universe '{config.name}/{config.version}' already exists with "
                        f"criteria_hash={existing_hash!r} but current config yields "
                        f"{criteria_hash!r}. Bump version to create a new definition."
                    )
            universe_id = universe_def.universe_id

        # ── 2. Load training members ──────────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            training_members = repo.load_scoring_company_inputs(
                training_universe_name=config.training_universe_name,
                training_universe_version=config.training_universe_version,
            )

        # ── 3. Validate manual tickers before evaluating ──────────────────────
        evaluator = ScoringUniverseEvaluator()
        evaluator.validate_manual_tickers(training_members, config)

        # ── 4. Evaluate ───────────────────────────────────────────────────────
        decisions = [
            evaluator.evaluate(c, config=config, sic_buckets=sic_buckets)
            for c in training_members
        ]

        # ── 5. Load active memberships ────────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)
            active_memberships = repo.list_active_memberships(universe_id)

        active_by_company = {m.company_id: m for m in active_memberships}
        decision_by_id = {d.company_id: d for d in decisions}

        included_decisions = [d for d in decisions if d.included]
        included_by_company = {d.company_id: d for d in included_decisions}

        # ── 6. Compute diffs ──────────────────────────────────────────────────
        newly_included = []
        already_included_count = 0
        security_changes = []
        newly_excluded = []

        for decision in included_decisions:
            existing = active_by_company.get(decision.company_id)
            if existing is None:
                newly_included.append(decision)
            elif existing.security_id != decision.security_id:
                security_changes.append((existing, decision))
            else:
                already_included_count += 1

        for company_id, existing in active_by_company.items():
            if company_id not in included_by_company:
                newly_excluded.append((existing, decision_by_id.get(company_id)))

        # ── 7. Persist atomically ─────────────────────────────────────────────
        with self._session_factory() as session:
            repo = UniverseRepository(session)

            for decision in newly_included:
                repo.insert_membership(
                    universe_id=universe_id,
                    company_id=decision.company_id,
                    security_id=decision.security_id,
                    included_from=as_of_date,
                    inclusion_reasons=decision.inclusion_reasons,
                )

            for existing, decision in security_changes:
                prev_ticker = (
                    (existing.inclusion_reasons or {})
                    .get("selected_security", {})
                    .get("ticker")
                )
                repo.close_membership(
                    universe_id=universe_id,
                    company_id=decision.company_id,
                    included_until=as_of_date,
                    exclusion_reasons={
                        "reason_codes": ["selected_security_changed"],
                        "details": {
                            "previous_security_id": str(existing.security_id),
                            "previous_ticker": prev_ticker,
                            "new_security_id": str(decision.security_id),
                        },
                    },
                )
                repo.insert_membership(
                    universe_id=universe_id,
                    company_id=decision.company_id,
                    security_id=decision.security_id,
                    included_from=as_of_date,
                    inclusion_reasons=decision.inclusion_reasons,
                )

            for existing, decision in newly_excluded:
                repo.close_membership(
                    universe_id=universe_id,
                    company_id=existing.company_id,
                    included_until=as_of_date,
                    exclusion_reasons=decision.exclusion_reasons if decision else {},
                )

            session.commit()

        # ── 8. Stats ──────────────────────────────────────────────────────────
        bucket_counts: dict[str, int] = {}
        exclusion_counts: dict[str, int] = {}
        bucket_inclusions = 0
        manual_inclusions = 0

        for d in decisions:
            if d.included:
                if d.inclusion_reasons.get("manual_inclusion"):
                    manual_inclusions += 1
                else:
                    bucket_inclusions += 1
                    for b in d.inclusion_reasons.get("matched_model_buckets", []):
                        bucket_counts[b] = bucket_counts.get(b, 0) + 1
            else:
                for code in d.exclusion_reasons.get("reason_codes", []):
                    exclusion_counts[code] = exclusion_counts.get(code, 0) + 1

        logger.info(
            "scoring_universe: evaluated=%d included=%d newly_included=%d "
            "security_changes=%d newly_excluded=%d universe_id=%s",
            len(decisions),
            len(included_decisions),
            len(newly_included),
            len(security_changes),
            len(newly_excluded),
            universe_id,
        )

        return ScoringUniverseResult(
            evaluated_training_members=len(decisions),
            included_companies=len(included_decisions),
            bucket_inclusions=bucket_inclusions,
            manual_inclusions=manual_inclusions,
            newly_included=len(newly_included),
            already_included=already_included_count,
            newly_excluded=len(newly_excluded),
            bucket_counts=bucket_counts,
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
