"""Canonical metric normalization service."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from invest_ml.canonical.classifier import CanonicalPeriodClassifier
from invest_ml.canonical.models import (
    CandidateFact,
    CanonicalMetricNormalizationResult,
    ResolvedCanonicalMetric,
)
from invest_ml.canonical.registry import CanonicalMetricRegistry
from invest_ml.canonical.resolver import CandidateResolver

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100


class CanonicalMetricNormalizationService:
    """Normalize xbrl_facts into canonical_metrics for all companies.

    For each (taxonomy, tag) registered in the registry, streams candidate
    facts in company batches, classifies period type, resolves the best
    candidate per group, and bulk-inserts resolved metrics with idempotency
    checking.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sf = session_factory

    def materialize(
        self,
        *,
        normalization_version: str,
        configuration: CanonicalMetricRegistry,
        company_ids: list[UUID] | None,
        ingested_at: datetime,
    ) -> CanonicalMetricNormalizationResult:
        from invest_ml.db.repositories.canonical_metrics import CanonicalMetricsRepository
        from invest_ml.db.repositories.xbrl_facts import XbrlFactsRepository

        classifier = CanonicalPeriodClassifier(configuration)
        resolver = CandidateResolver(
            annual_duration_center=(
                configuration.annual_duration_min + configuration.annual_duration_max
            )
            / 2.0,
            quarterly_duration_center=(
                configuration.quarterly_duration_min + configuration.quarterly_duration_max
            )
            / 2.0,
        )

        taxonomy_tags = configuration.taxonomy_tags()

        # Running totals
        companies_considered = 0
        companies_with_metrics = 0
        metrics_resolved = 0
        metrics_created = 0
        metrics_already_present = 0
        annual_metrics_resolved = 0
        quarterly_metrics_resolved = 0
        facts_considered = 0
        facts_rejected = 0
        candidate_groups = 0
        coverage: dict[str, dict[str, int]] = {
            name: {"annual": 0, "quarter": 0} for name in configuration.metrics
        }

        with self._sf() as session:
            xbrl_repo = XbrlFactsRepository(session)
            canonical_repo = CanonicalMetricsRepository(session)

            for fact_batch in xbrl_repo.stream_candidate_facts(
                taxonomy_tags=taxonomy_tags,
                company_ids=company_ids,
                batch_size=_BATCH_SIZE,
            ):
                facts_by_company: dict[UUID, list] = defaultdict(list)
                for fact in fact_batch:
                    facts_by_company[fact.company_id].append(fact)

                metrics_to_insert: list[ResolvedCanonicalMetric] = []

                for company_id, company_facts in facts_by_company.items():
                    companies_considered += 1

                    company_fc, company_fr, company_cg, company_resolved = (
                        self._normalize_company(
                            company_id=company_id,
                            facts=company_facts,
                            configuration=configuration,
                            classifier=classifier,
                            resolver=resolver,
                            normalization_version=normalization_version,
                        )
                    )
                    facts_considered += company_fc
                    facts_rejected += company_fr
                    candidate_groups += company_cg

                    if company_resolved:
                        companies_with_metrics += 1
                        for m in company_resolved:
                            metrics_resolved += 1
                            coverage[m.metric_name][m.period_type] += 1
                            if m.period_type == "annual":
                                annual_metrics_resolved += 1
                            elif m.period_type == "quarter":
                                quarterly_metrics_resolved += 1
                        metrics_to_insert.extend(company_resolved)

                if metrics_to_insert:
                    insert_result = canonical_repo.bulk_insert_metrics(
                        metrics_to_insert,
                        ingested_at=ingested_at,
                    )
                    metrics_created += insert_result.rows_inserted
                    metrics_already_present += insert_result.rows_already_present

                session.commit()

        return CanonicalMetricNormalizationResult(
            normalization_version=normalization_version,
            configuration_hash=configuration.configuration_hash,
            companies_considered=companies_considered,
            companies_with_metrics=companies_with_metrics,
            metrics_resolved=metrics_resolved,
            metrics_created=metrics_created,
            metrics_already_present=metrics_already_present,
            annual_metrics_resolved=annual_metrics_resolved,
            quarterly_metrics_resolved=quarterly_metrics_resolved,
            facts_considered=facts_considered,
            facts_rejected=facts_rejected,
            candidate_groups=candidate_groups,
            coverage=coverage,
        )

    def _normalize_company(
        self,
        *,
        company_id: UUID,
        facts,
        configuration: CanonicalMetricRegistry,
        classifier: CanonicalPeriodClassifier,
        resolver: CandidateResolver,
        normalization_version: str,
    ) -> tuple[int, int, int, list[ResolvedCanonicalMetric]]:
        """Return (facts_considered, facts_rejected, candidate_groups, resolved_metrics)."""
        facts_considered = 0
        facts_rejected = 0

        # (metric_name, period_type, period_start, period_end, available_at) → candidates
        groups: dict[tuple, list] = defaultdict(list)

        for fact in facts:
            facts_considered += 1

            lookup = configuration.find_concept(fact.taxonomy, fact.tag)
            if lookup is None:
                facts_rejected += 1
                continue
            metric_name, concept_config = lookup
            metric_config = configuration.metrics[metric_name]

            if fact.unit not in metric_config.expected_units:
                facts_rejected += 1
                continue

            classification = classifier.classify(
                metric_period_kind=metric_config.period_kind,
                period_start=fact.period_start,
                period_end=fact.period_end,
                form=fact.form,
                fiscal_period=fact.fiscal_period,
            )
            if not classification.supported:
                facts_rejected += 1
                continue

            available_at = fact.filed_date
            group_key = (
                metric_name,
                classification.period_type,
                fact.period_start,
                fact.period_end,
                available_at,
            )
            raw_value = fact.value
            value = raw_value if isinstance(raw_value, Decimal) else Decimal(str(raw_value))
            candidate_fact = CandidateFact(
                fact_id=fact.fact_id,
                company_id=fact.company_id,
                taxonomy=fact.taxonomy,
                tag=fact.tag,
                unit=fact.unit,
                period_start=fact.period_start,
                period_end=fact.period_end,
                value=value,
                accession_number=fact.accession_number,
                fiscal_year=fact.fiscal_year,
                fiscal_period=fact.fiscal_period,
                form=fact.form,
                filed_date=fact.filed_date,
            )
            groups[group_key].append((candidate_fact, concept_config, classification))

        candidate_groups = len(groups)
        results: list[ResolvedCanonicalMetric] = []

        for (metric_name, period_type, period_start, period_end, available_at), candidates in groups.items():
            metric_config = configuration.metrics[metric_name]

            winner_fact, winner_concept, quality_flags = resolver.resolve(
                candidates,
                metric_config=metric_config,
                period_type=period_type,
            )

            results.append(
                ResolvedCanonicalMetric(
                    company_id=company_id,
                    metric_name=metric_name,
                    period_type=period_type,
                    fiscal_year=winner_fact.fiscal_year,
                    fiscal_period=winner_fact.fiscal_period,
                    period_start=period_start,
                    period_end=period_end,
                    available_at=available_at,
                    value=winner_fact.value,
                    unit=winner_fact.unit,
                    normalization_version=normalization_version,
                    source_fact_ids=[winner_fact.fact_id],
                    derivation={
                        "type": "direct_fact",
                        "taxonomy": winner_fact.taxonomy,
                        "tag": winner_fact.tag,
                        "concept_priority": winner_concept.priority,
                        "source_fact_id": winner_fact.fact_id,
                        "configuration_hash": configuration.configuration_hash,
                    },
                    quality_flags=quality_flags,
                )
            )

        return facts_considered, facts_rejected, candidate_groups, results
