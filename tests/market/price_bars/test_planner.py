"""Tests for the price-bar request planner."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from invest_ml.market.price_bars.models import (
    PriceBarCoverage,
    SelectedPriceSecurity,
    SyncStateData,
)
from invest_ml.market.price_bars.planner import build_request_plan

_TARGET = date(2026, 7, 10)
_BACKFILL_START = date(2015, 1, 1)
_OVERLAP_DAYS = 14
_NOW = datetime(2026, 7, 10, 20, 0, 0, tzinfo=UTC)


def _sec(ticker: str = "AAPL") -> SelectedPriceSecurity:
    return SelectedPriceSecurity(
        security_id=uuid4(), company_id=uuid4(), ticker=ticker, exchange="NASDAQ"
    )


def _coverage(sec: SelectedPriceSecurity, earliest: date, latest: date, count: int = 100) -> PriceBarCoverage:
    return PriceBarCoverage(
        security_id=sec.security_id,
        earliest_stored_date=earliest,
        latest_stored_date=latest,
        stored_row_count=count,
    )


def _sync_state(
    sec: SelectedPriceSecurity,
    *,
    status: str = "succeeded",
    checked_through: date | None = None,
    next_retry_at: datetime | None = None,
    consecutive_failures: int = 0,
    last_recon_ca: date | None = None,
) -> SyncStateData:
    return SyncStateData(
        security_id=sec.security_id,
        source="tiingo_eod",
        backfill_start_date=_BACKFILL_START,
        latest_stored_trading_date=None,
        checked_through_date=checked_through,
        last_attempted_at=None,
        last_succeeded_at=None,
        last_full_refresh_at=None,
        last_reconciled_corporate_action_date=last_recon_ca,
        status=status,
        consecutive_failures=consecutive_failures,
        next_retry_at=next_retry_at,
        last_error=None,
    )


def _plan(secs, coverage=None, sync_states=None):
    cov = coverage or {}
    states = sync_states or {}
    return build_request_plan(
        securities=secs,
        coverage=cov,
        sync_states=states,
        target_end_date=_TARGET,
        backfill_start_date=_BACKFILL_START,
        incremental_overlap_days=_OVERLAP_DAYS,
        now=_NOW,
    )


def test_no_history_plans_initial_backfill():
    sec = _sec()
    plan = _plan([sec])

    assert plan.securities_requiring_initial_backfill == 1
    assert plan.estimated_provider_requests == 1
    assert len(plan.requests) == 1
    req = plan.requests[0]
    assert req.mode == "initial_backfill"
    assert req.start_date == _BACKFILL_START
    assert req.end_date == _TARGET


def test_current_checked_through_plans_no_request():
    sec = _sec()
    state = _sync_state(sec, status="succeeded", checked_through=_TARGET)
    plan = _plan([sec], sync_states={sec.security_id: state})

    assert plan.securities_already_current == 1
    assert plan.estimated_provider_requests == 0
    assert plan.requests == ()


def test_existing_history_plans_incremental():
    sec = _sec()
    cov = _coverage(sec, earliest=_BACKFILL_START, latest=date(2026, 7, 1))
    plan = _plan([sec], coverage={sec.security_id: cov})

    assert plan.securities_requiring_incremental_update == 1
    req = plan.requests[0]
    assert req.mode == "incremental"
    expected_start = date(2026, 7, 1) - timedelta(days=_OVERLAP_DAYS)
    assert req.start_date == expected_start
    assert req.end_date == _TARGET


def test_partial_head_history_plans_full_reconciliation():
    sec = _sec()
    # earliest is 2020 but backfill_start is 2015 → gap at head
    cov = _coverage(sec, earliest=date(2020, 1, 2), latest=date(2026, 7, 1))
    plan = _plan([sec], coverage={sec.security_id: cov})

    assert plan.securities_requiring_full_reconciliation == 1
    req = plan.requests[0]
    assert req.mode == "full_reconciliation"
    assert req.start_date == _BACKFILL_START


def test_retry_deferred_security_is_skipped():
    sec = _sec()
    future_retry = _NOW + timedelta(hours=2)
    state = _sync_state(sec, status="failed", next_retry_at=future_retry, consecutive_failures=2)
    plan = _plan([sec], sync_states={sec.security_id: state})

    assert plan.securities_retry_deferred == 1
    assert plan.estimated_provider_requests == 0


def test_past_retry_at_triggers_new_request():
    sec = _sec()
    past_retry = _NOW - timedelta(hours=1)
    state = _sync_state(sec, status="failed", next_retry_at=past_retry, consecutive_failures=1)
    plan = _plan([sec], sync_states={sec.security_id: state})

    assert plan.estimated_provider_requests == 1
    assert plan.securities_retry_deferred == 0


def test_unsupported_security_is_skipped():
    sec = _sec()
    state = _sync_state(sec, status="unsupported")
    plan = _plan([sec], sync_states={sec.security_id: state})

    assert plan.securities_unsupported == 1
    assert plan.estimated_provider_requests == 0


def test_estimated_request_count_matches_requests():
    secs = [_sec(f"S{i}") for i in range(5)]
    plan = _plan(secs)
    assert plan.estimated_provider_requests == len(plan.requests)


def test_incremental_overlap_clamped_to_backfill_start():
    sec = _sec()
    # latest_stored is very close to backfill_start
    cov = _coverage(sec, earliest=_BACKFILL_START, latest=date(2015, 1, 10))
    plan = _plan([sec], coverage={sec.security_id: cov})

    req = plan.requests[0]
    assert req.mode == "incremental"
    # start must not go before backfill_start
    assert req.start_date >= _BACKFILL_START


def test_checked_through_before_target_triggers_incremental():
    sec = _sec()
    old_checked = date(2026, 7, 1)  # before target
    state = _sync_state(sec, status="succeeded", checked_through=old_checked)
    cov = _coverage(sec, earliest=_BACKFILL_START, latest=old_checked)
    plan = _plan([sec], coverage={sec.security_id: cov}, sync_states={sec.security_id: state})

    assert plan.securities_requiring_incremental_update == 1
    assert plan.securities_already_current == 0


def test_mixed_securities_classified_correctly():
    s1 = _sec("CURRENT")  # already current
    s2 = _sec("NEW")      # no history
    s3 = _sec("INC")      # incremental
    s4 = _sec("DEFER")    # retry-deferred
    s5 = _sec("UNSUP")    # unsupported

    cov_s3 = _coverage(s3, earliest=_BACKFILL_START, latest=date(2026, 7, 1))
    state_s1 = _sync_state(s1, status="succeeded", checked_through=_TARGET)
    state_s4 = _sync_state(s4, status="failed", next_retry_at=_NOW + timedelta(hours=1), consecutive_failures=1)
    state_s5 = _sync_state(s5, status="unsupported")

    plan = _plan(
        [s1, s2, s3, s4, s5],
        coverage={s3.security_id: cov_s3},
        sync_states={
            s1.security_id: state_s1,
            s4.security_id: state_s4,
            s5.security_id: state_s5,
        },
    )
    assert plan.securities_already_current == 1
    assert plan.securities_requiring_initial_backfill == 1
    assert plan.securities_requiring_incremental_update == 1
    assert plan.securities_retry_deferred == 1
    assert plan.securities_unsupported == 1
    assert plan.estimated_provider_requests == 2  # s2 + s3
