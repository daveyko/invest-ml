"""Pure request planner for selected-price-bars ingestion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from uuid import UUID

from invest_ml.market.price_bars.models import (
    PriceBarCoverage,
    PriceBarRequestPlan,
    SecurityPriceRequest,
    SelectedPriceSecurity,
    SyncStateData,
)

_MODE_INITIAL = "initial_backfill"
_MODE_INCREMENTAL = "incremental"
_MODE_FULL = "full_reconciliation"

_STATUS_SUCCEEDED = "succeeded"
_STATUS_FAILED = "failed"
_STATUS_UNSUPPORTED = "unsupported"


def build_request_plan(
    *,
    securities: Sequence[SelectedPriceSecurity],
    coverage: Mapping[UUID, PriceBarCoverage],
    sync_states: Mapping[UUID, SyncStateData],
    target_end_date: date,
    backfill_start_date: date,
    incremental_overlap_days: int,
    now: datetime,
) -> PriceBarRequestPlan:
    """Classify each security and build the minimal set of provider requests.

    Skip conditions (no request issued):
    - status == succeeded AND checked_through_date >= target_end_date
    - status == unsupported
    - status == failed AND next_retry_at > now

    Request modes:
    - initial_backfill: no stored bars
    - full_reconciliation: earliest_stored_date > backfill_start_date
    - incremental: has history from backfill_start; use overlap window
    """
    already_current = 0
    initial_backfills = 0
    incremental = 0
    full_reconciliation = 0
    retry_deferred = 0
    unsupported = 0

    requests: list[SecurityPriceRequest] = []

    for sec in securities:
        sid = sec.security_id
        state = sync_states.get(sid)
        cov = coverage.get(sid)

        # Already-current skip
        if (
            state is not None
            and state.status == _STATUS_SUCCEEDED
            and state.checked_through_date is not None
            and state.checked_through_date >= target_end_date
        ):
            already_current += 1
            continue

        # Unsupported skip
        if state is not None and state.status == _STATUS_UNSUPPORTED:
            unsupported += 1
            continue

        # Retry-deferred skip
        if (
            state is not None
            and state.status == _STATUS_FAILED
            and state.next_retry_at is not None
            and state.next_retry_at > now
        ):
            retry_deferred += 1
            continue

        # Determine request mode
        if cov is None or cov.stored_row_count == 0:
            mode = _MODE_INITIAL
            req_start = backfill_start_date
            initial_backfills += 1
        elif cov.earliest_stored_date is not None and cov.earliest_stored_date > backfill_start_date:
            mode = _MODE_FULL
            req_start = backfill_start_date
            full_reconciliation += 1
        else:
            mode = _MODE_INCREMENTAL
            latest = cov.latest_stored_date or backfill_start_date
            overlap_start = latest - timedelta(days=incremental_overlap_days)
            req_start = max(overlap_start, backfill_start_date)
            incremental += 1

        requests.append(
            SecurityPriceRequest(
                security_id=sid,
                ticker=sec.ticker,
                mode=mode,
                start_date=req_start,
                end_date=target_end_date,
            )
        )

    return PriceBarRequestPlan(
        selected_securities=len(securities),
        target_end_date=target_end_date,
        securities_already_current=already_current,
        securities_requiring_initial_backfill=initial_backfills,
        securities_requiring_incremental_update=incremental,
        securities_requiring_full_reconciliation=full_reconciliation,
        securities_retry_deferred=retry_deferred,
        securities_unsupported=unsupported,
        estimated_provider_requests=len(requests),
        requests=tuple(requests),
    )
