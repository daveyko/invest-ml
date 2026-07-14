"""SelectedPriceBarsService: orchestrates EOD bar ingestion for training securities."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from invest_ml.market.errors import (
    MarketDataInstrumentNotFoundError,
    MarketDataRateLimitError,
)
from invest_ml.market.models import DailyBar
from invest_ml.market.price_bars.models import (
    PriceBarRequestPlan,
    SecurityPriceRequest,
    SelectedPriceBarsResult,
    SelectedPriceSecurity,
    SyncStateData,
)
from invest_ml.market.price_bars.planner import build_request_plan
from invest_ml.market.price_bars.provider import DailyPriceProvider
from invest_ml.market.price_bars.validator import validate_bars

logger = logging.getLogger(__name__)

_SOURCE = "tiingo_eod"

_ZERO = Decimal("0")
_ONE = Decimal("1")


class SelectedPriceBarsService:
    """Normalize and persist daily price bars for selected training securities.

    Architecture
    ------------
    - All provider HTTP calls run in a bounded ThreadPoolExecutor.
    - All database work runs on the main thread (sessions are not thread-safe).
    - Securities are processed in batches; each batch is committed atomically.
    - A failed ticker does not roll back successful tickers in the same batch.
    """

    def __init__(
        self,
        price_provider: DailyPriceProvider,
        session_factory: Callable[[], Session],
    ) -> None:
        self._provider = price_provider
        self._sf = session_factory

    def materialize(
        self,
        *,
        selected_securities: Sequence[SelectedPriceSecurity],
        target_end_date: date,
        backfill_start_date: date,
        ingestion_run_id: UUID,
        max_concurrency: int = 4,
        security_batch_size: int = 25,
        insert_batch_size: int = 10_000,
        incremental_overlap_days: int = 14,
        source: str = _SOURCE,
        max_failed_securities: int = 25,
        max_failed_security_ratio: float = 0.02,
    ) -> SelectedPriceBarsResult:
        from invest_ml.db.repositories.price_bars import PriceBarsRepository

        if not selected_securities:
            return _empty_result(target_end_date)

        now = datetime.now(tz=UTC)

        # Bulk-load coverage and sync states once for all securities
        all_ids = [s.security_id for s in selected_securities]
        with self._sf() as session:
            repo = PriceBarsRepository(session)
            coverage = dict(repo.get_price_bar_coverage(security_ids=all_ids, source=source))
            sync_states = dict(repo.get_sync_states(security_ids=all_ids, source=source))

        plan = build_request_plan(
            securities=selected_securities,
            coverage=coverage,
            sync_states=sync_states,
            target_end_date=target_end_date,
            backfill_start_date=backfill_start_date,
            incremental_overlap_days=incremental_overlap_days,
            now=now,
        )

        logger.info(
            "Price-bar plan: %d selected, %d requests, %d already-current, "
            "%d initial, %d incremental, %d full-reconciliation",
            plan.selected_securities,
            plan.estimated_provider_requests,
            plan.securities_already_current,
            plan.securities_requiring_initial_backfill,
            plan.securities_requiring_incremental_update,
            plan.securities_requiring_full_reconciliation,
        )

        if not plan.requests:
            return SelectedPriceBarsResult(
                plan=plan,
                securities_requested=0,
                securities_skipped=plan.securities_already_current
                + plan.securities_retry_deferred
                + plan.securities_unsupported,
                securities_succeeded=0,
                securities_failed=0,
                securities_unsupported=plan.securities_unsupported,
                initial_backfills=0,
                incremental_updates=0,
                full_reconciliations=0,
                provider_requests=0,
                provider_retries=0,
                provider_rate_limits=0,
                bars_received=0,
                rows_inserted=0,
                rows_updated=0,
                rows_unchanged=0,
                invalid_rows=0,
                earliest_bar_date=None,
                latest_bar_date=None,
            )

        # Totals
        provider_requests = 0
        provider_retries = 0
        provider_rate_limits = 0
        bars_received = 0
        rows_inserted_total = 0
        rows_updated_total = 0
        rows_unchanged_total = 0
        invalid_rows_total = 0
        succeeded = 0
        failed_count = 0
        unsupported_count = plan.securities_unsupported
        initial_backfills_done = 0
        incremental_done = 0
        full_recon_done = 0

        earliest_bar_date: date | None = None
        latest_bar_date: date | None = None

        requests_list = list(plan.requests)

        for batch_start in range(0, len(requests_list), security_batch_size):
            batch = requests_list[batch_start : batch_start + security_batch_size]

            # ── Fetch bars concurrently ────────────────────────────────────
            fetch_results: list[tuple[SecurityPriceRequest, list[DailyBar] | Exception]] = []

            with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
                future_to_req = {
                    executor.submit(
                        _fetch_bars, self._provider, req
                    ): req
                    for req in batch
                }
                for fut in as_completed(future_to_req):
                    req = future_to_req[fut]
                    exc_or_bars = fut.result()  # _fetch_bars never raises
                    fetch_results.append((req, exc_or_bars))

            provider_requests += len(batch)

            # ── Detect securities needing full reconciliation ───────────────
            recon_requests: list[SecurityPriceRequest] = []

            for req, result in fetch_results:
                if isinstance(result, Exception):
                    continue
                if req.mode == "incremental":
                    state = sync_states.get(req.security_id)
                    if _needs_full_reconciliation(
                        bars=result,
                        request=req,
                        sync_state=state,
                        backfill_start_date=backfill_start_date,
                    ):
                        recon_req = SecurityPriceRequest(
                            security_id=req.security_id,
                            ticker=req.ticker,
                            mode="full_reconciliation",
                            start_date=backfill_start_date,
                            end_date=target_end_date,
                        )
                        recon_requests.append(recon_req)
                        logger.info(
                            "Full reconciliation triggered for %r (%s)",
                            req.ticker,
                            req.security_id,
                        )

            # ── Fetch full-reconciliation bars concurrently ────────────────
            if recon_requests:
                provider_requests += len(recon_requests)
                with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
                    future_to_req = {
                        executor.submit(_fetch_bars, self._provider, req): req
                        for req in recon_requests
                    }
                    recon_by_id: dict[UUID, list[DailyBar] | Exception] = {}
                    for fut in as_completed(future_to_req):
                        req = future_to_req[fut]
                        recon_by_id[req.security_id] = fut.result()

                # Merge: replace incremental results with reconciliation results
                updated_results: list[tuple[SecurityPriceRequest, list[DailyBar] | Exception]] = []
                replaced_ids = {r.security_id for r in recon_requests}
                for req, result in fetch_results:
                    if req.security_id in replaced_ids:
                        updated_results.append(
                            (
                                SecurityPriceRequest(
                                    security_id=req.security_id,
                                    ticker=req.ticker,
                                    mode="full_reconciliation",
                                    start_date=backfill_start_date,
                                    end_date=target_end_date,
                                ),
                                recon_by_id[req.security_id],
                            )
                        )
                    else:
                        updated_results.append((req, result))
                fetch_results = updated_results

            # ── Persist: one session per batch ─────────────────────────────
            with self._sf() as session:
                repo = PriceBarsRepository(session)

                for req, result in fetch_results:
                    now_ts = datetime.now(tz=UTC)
                    state = sync_states.get(req.security_id)

                    if isinstance(result, Exception):
                        exc = result
                        logger.warning(
                            "Price-bar fetch failed for %r (%s): %s",
                            req.ticker,
                            req.security_id,
                            type(exc).__name__,
                        )
                        failed_count += 1

                        if isinstance(exc, MarketDataInstrumentNotFoundError):
                            _upsert_sync_state_unsupported(
                                repo,
                                req=req,
                                source=source,
                                backfill_start_date=backfill_start_date,
                                last_attempted_at=now_ts,
                                last_error=str(exc)[:500],
                                state=state,
                            )
                            unsupported_count += 1
                            failed_count -= 1
                        else:
                            if isinstance(exc, MarketDataRateLimitError):
                                provider_rate_limits += 1
                            consecutive = (state.consecutive_failures + 1) if state else 1
                            backoff_secs = min(60 * (2 ** (consecutive - 1)), 3600)
                            next_retry = now_ts + timedelta(seconds=backoff_secs)
                            _upsert_sync_state_failed(
                                repo,
                                req=req,
                                source=source,
                                backfill_start_date=backfill_start_date,
                                last_attempted_at=now_ts,
                                last_error=str(exc)[:500],
                                state=state,
                                consecutive_failures=consecutive,
                                next_retry_at=next_retry,
                            )
                        continue

                    bars = result

                    # Validate
                    valid_bars, rejections = validate_bars(
                        bars,
                        ticker=req.ticker,
                        start_date=req.start_date,
                        end_date=req.end_date,
                    )
                    if rejections:
                        for reason in rejections[:5]:
                            logger.warning("Bar validation: %s", reason)
                    invalid_rows_total += len(bars) - len(valid_bars)
                    bars_received += len(valid_bars)

                    # Upsert
                    ins, upd, unch = repo.bulk_upsert_price_bars(
                        security_id=req.security_id,
                        source=source,
                        source_ticker=req.ticker,
                        bars=valid_bars,
                        ingested_at=now_ts,
                        batch_size=insert_batch_size,
                    )
                    rows_inserted_total += ins
                    rows_updated_total += upd
                    rows_unchanged_total += unch

                    # Track date range
                    for b in valid_bars:
                        if earliest_bar_date is None or b.trading_date < earliest_bar_date:
                            earliest_bar_date = b.trading_date
                        if latest_bar_date is None or b.trading_date > latest_bar_date:
                            latest_bar_date = b.trading_date

                    # Compute latest_stored for sync state
                    latest_stored: date | None = None
                    if valid_bars:
                        latest_stored = max(b.trading_date for b in valid_bars)
                    elif state and state.latest_stored_trading_date:
                        latest_stored = state.latest_stored_trading_date

                    # Determine reconciliation metadata
                    last_full_refresh_at = state.last_full_refresh_at if state else None
                    last_recon_ca_date = (
                        state.last_reconciled_corporate_action_date if state else None
                    )
                    if req.mode == "full_reconciliation":
                        last_full_refresh_at = now_ts
                        ca_date = _latest_corporate_action_date(valid_bars)
                        if ca_date is not None:
                            last_recon_ca_date = ca_date

                    # Upsert sync state
                    repo.upsert_sync_state(
                        security_id=req.security_id,
                        source=source,
                        status="succeeded",
                        backfill_start_date=backfill_start_date,
                        checked_through_date=target_end_date,
                        latest_stored_trading_date=latest_stored,
                        last_attempted_at=now_ts,
                        last_succeeded_at=now_ts,
                        last_full_refresh_at=last_full_refresh_at,
                        last_reconciled_corporate_action_date=last_recon_ca_date,
                        consecutive_failures=0,
                        next_retry_at=None,
                        last_error=None,
                    )

                    succeeded += 1
                    if req.mode == "initial_backfill":
                        initial_backfills_done += 1
                    elif req.mode == "incremental":
                        incremental_done += 1
                    elif req.mode == "full_reconciliation":
                        full_recon_done += 1

                session.commit()

            # Abort if failure threshold exceeded
            total_attempted = succeeded + failed_count
            if total_attempted > 0 and failed_count > max_failed_securities:
                ratio = failed_count / total_attempted
                if ratio > max_failed_security_ratio:
                    raise RuntimeError(
                        f"Price-bar ingestion aborted: {failed_count}/{total_attempted} "
                        f"securities failed (ratio={ratio:.2%} > {max_failed_security_ratio:.2%})"
                    )

        skipped = (
            plan.securities_already_current
            + plan.securities_retry_deferred
            + plan.securities_unsupported
        )

        return SelectedPriceBarsResult(
            plan=plan,
            securities_requested=len(plan.requests),
            securities_skipped=skipped,
            securities_succeeded=succeeded,
            securities_failed=failed_count,
            securities_unsupported=unsupported_count,
            initial_backfills=initial_backfills_done,
            incremental_updates=incremental_done,
            full_reconciliations=full_recon_done,
            provider_requests=provider_requests,
            provider_retries=provider_retries,
            provider_rate_limits=provider_rate_limits,
            bars_received=bars_received,
            rows_inserted=rows_inserted_total,
            rows_updated=rows_updated_total,
            rows_unchanged=rows_unchanged_total,
            invalid_rows=invalid_rows_total,
            earliest_bar_date=earliest_bar_date,
            latest_bar_date=latest_bar_date,
        )


def _fetch_bars(
    provider: DailyPriceProvider,
    req: SecurityPriceRequest,
) -> list[DailyBar] | Exception:
    """Fetch bars for one request; always returns, never raises."""
    try:
        bars = provider.get_daily_bars(
            ticker=req.ticker,
            start_date=req.start_date,
            end_date=req.end_date,
        )
        return list(bars)
    except Exception as exc:
        return exc


def _needs_full_reconciliation(
    *,
    bars: list[DailyBar],
    request: SecurityPriceRequest,
    sync_state: SyncStateData | None,
    backfill_start_date: date,
) -> bool:
    """True if an incremental response reveals a corporate action that hasn't been reconciled."""
    if not bars:
        return False

    last_recon = (
        sync_state.last_reconciled_corporate_action_date if sync_state else None
    )

    for bar in bars:
        is_new_action = last_recon is None or bar.trading_date > last_recon
        has_dividend = bar.dividend_cash is not None and bar.dividend_cash != _ZERO
        has_split = bar.split_factor is not None and bar.split_factor != _ONE

        if is_new_action and (has_dividend or has_split):
            return True

    return False


def _latest_corporate_action_date(bars: list[DailyBar]) -> date | None:
    result: date | None = None
    for bar in bars:
        has_dividend = bar.dividend_cash is not None and bar.dividend_cash != _ZERO
        has_split = bar.split_factor is not None and bar.split_factor != _ONE
        if has_dividend or has_split:
            if result is None or bar.trading_date > result:
                result = bar.trading_date
    return result


def _upsert_sync_state_failed(
    repo,
    *,
    req: SecurityPriceRequest,
    source: str,
    backfill_start_date: date,
    last_attempted_at: datetime,
    last_error: str,
    state: SyncStateData | None,
    consecutive_failures: int,
    next_retry_at: datetime,
) -> None:
    repo.upsert_sync_state(
        security_id=req.security_id,
        source=source,
        status="failed",
        backfill_start_date=backfill_start_date,
        checked_through_date=state.checked_through_date if state else None,
        latest_stored_trading_date=state.latest_stored_trading_date if state else None,
        last_attempted_at=last_attempted_at,
        last_succeeded_at=state.last_succeeded_at if state else None,
        last_full_refresh_at=state.last_full_refresh_at if state else None,
        last_reconciled_corporate_action_date=(
            state.last_reconciled_corporate_action_date if state else None
        ),
        consecutive_failures=consecutive_failures,
        next_retry_at=next_retry_at,
        last_error=last_error,
    )


def _upsert_sync_state_unsupported(
    repo,
    *,
    req: SecurityPriceRequest,
    source: str,
    backfill_start_date: date,
    last_attempted_at: datetime,
    last_error: str,
    state: SyncStateData | None,
) -> None:
    repo.upsert_sync_state(
        security_id=req.security_id,
        source=source,
        status="unsupported",
        backfill_start_date=backfill_start_date,
        checked_through_date=state.checked_through_date if state else None,
        latest_stored_trading_date=state.latest_stored_trading_date if state else None,
        last_attempted_at=last_attempted_at,
        last_succeeded_at=state.last_succeeded_at if state else None,
        last_full_refresh_at=state.last_full_refresh_at if state else None,
        last_reconciled_corporate_action_date=(
            state.last_reconciled_corporate_action_date if state else None
        ),
        consecutive_failures=0,
        next_retry_at=None,
        last_error=last_error,
    )


def _empty_result(target_end_date: date) -> SelectedPriceBarsResult:

    empty_plan = PriceBarRequestPlan(
        selected_securities=0,
        target_end_date=target_end_date,
        securities_already_current=0,
        securities_requiring_initial_backfill=0,
        securities_requiring_incremental_update=0,
        securities_requiring_full_reconciliation=0,
        securities_retry_deferred=0,
        securities_unsupported=0,
        estimated_provider_requests=0,
        requests=(),
    )
    return SelectedPriceBarsResult(
        plan=empty_plan,
        securities_requested=0,
        securities_skipped=0,
        securities_succeeded=0,
        securities_failed=0,
        securities_unsupported=0,
        initial_backfills=0,
        incremental_updates=0,
        full_reconciliations=0,
        provider_requests=0,
        provider_retries=0,
        provider_rate_limits=0,
        bars_received=0,
        rows_inserted=0,
        rows_updated=0,
        rows_unchanged=0,
        invalid_rows=0,
        earliest_bar_date=None,
        latest_bar_date=None,
    )
