"""Repository for canonical_metrics reads and writes."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from invest_ml.canonical.models import (
    CanonicalMetricInsertResult,
    ResolvedCanonicalMetric,
)
from invest_ml.db.models.financials import CanonicalMetric

logger = logging.getLogger(__name__)


class CanonicalMetricsRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def bulk_insert_metrics(
        self,
        metrics: list[ResolvedCanonicalMetric],
        *,
        ingested_at: datetime,
    ) -> CanonicalMetricInsertResult:
        """Insert resolved canonical metrics with idempotency checking.

        Queries existing rows by company_id + normalization_version (simple IN)
        rather than a 6-column tuple IN, which avoids PostgreSQL's stack depth
        and parameter count limits. The service already batches by company so
        company_id sets here are naturally small (~100 IDs).
        """
        if not metrics:
            return CanonicalMetricInsertResult(
                rows_seen=0, rows_inserted=0, rows_already_present=0, conflicting_rows=0
            )

        # Fetch existing rows for the same companies + normalization version.
        # company_id IN (<100 UUIDs>) is cheap and avoids huge tuple IN clauses.
        company_ids = list({m.company_id for m in metrics})
        norm_versions = list({m.normalization_version for m in metrics})

        existing_rows = (
            self._s.execute(
                select(CanonicalMetric).where(
                    CanonicalMetric.company_id.in_(company_ids),
                    CanonicalMetric.normalization_version.in_(norm_versions),
                )
            )
            .scalars()
            .all()
        )

        existing_map: dict[tuple, CanonicalMetric] = {}
        for row in existing_rows:
            key = (
                row.company_id,
                row.metric_name,
                row.period_type,
                row.period_end,
                row.available_at,
                row.normalization_version,
            )
            existing_map[key] = row

        to_insert: list[ResolvedCanonicalMetric] = []
        already_present = 0
        conflicts: list[tuple[CanonicalMetric, ResolvedCanonicalMetric]] = []

        for m in metrics:
            key = (
                m.company_id,
                m.metric_name,
                m.period_type,
                m.period_end,
                m.available_at,
                m.normalization_version,
            )
            existing = existing_map.get(key)
            if existing is None:
                to_insert.append(m)
            elif _rows_equivalent(existing, m):
                already_present += 1
            else:
                conflicts.append((existing, m))

        if conflicts:
            _raise_conflict_error(conflicts)

        if to_insert:
            self._s.execute(
                pg_insert(CanonicalMetric).on_conflict_do_nothing(
                    constraint="uq_canonical_metrics"
                ),
                [_to_row(m, ingested_at) for m in to_insert],
            )

        return CanonicalMetricInsertResult(
            rows_seen=len(metrics),
            rows_inserted=len(to_insert),
            rows_already_present=already_present,
            conflicting_rows=len(conflicts),
        )


def _rows_equivalent(existing: CanonicalMetric, proposed: ResolvedCanonicalMetric) -> bool:
    existing_val = (
        existing.value
        if isinstance(existing.value, Decimal)
        else Decimal(str(existing.value))
    )
    return (
        existing_val == proposed.value
        and existing.unit == proposed.unit
        and set(existing.source_fact_ids) == set(proposed.source_fact_ids)
    )


def _raise_conflict_error(
    conflicts: list[tuple[CanonicalMetric, ResolvedCanonicalMetric]],
) -> None:
    lines = [
        f"Canonical metric idempotency conflict: {len(conflicts)} row(s) differ from existing DB rows:"
    ]
    for existing, proposed in conflicts[:5]:
        lines.append(
            f"  company={proposed.company_id} metric={proposed.metric_name} "
            f"period_type={proposed.period_type} period_end={proposed.period_end} "
            f"available_at={proposed.available_at}: "
            f"DB value={existing.value!r} unit={existing.unit!r}, "
            f"proposed value={proposed.value!r} unit={proposed.unit!r}"
        )
    if len(conflicts) > 5:
        lines.append(f"  ... and {len(conflicts) - 5} more conflict(s)")
    raise ValueError("\n".join(lines))


def _to_row(m: ResolvedCanonicalMetric, ingested_at: datetime) -> dict:
    return {
        "company_id": m.company_id,
        "metric_name": m.metric_name,
        "period_type": m.period_type,
        "fiscal_year": m.fiscal_year,
        "fiscal_period": m.fiscal_period,
        "period_start": m.period_start,
        "period_end": m.period_end,
        "available_at": m.available_at,
        "value": m.value,
        "unit": m.unit,
        "normalization_version": m.normalization_version,
        "source_fact_ids": m.source_fact_ids,
        "derivation": m.derivation,
        "quality_flags": m.quality_flags,
        "created_at": ingested_at,
    }
