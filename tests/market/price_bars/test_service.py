"""Tests for SelectedPriceBarsService using mock providers and sessions."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from invest_ml.market.models import DailyBar
from invest_ml.market.price_bars.models import (
    PriceBarCoverage,
    SelectedPriceSecurity,
    SyncStateData,
)
from invest_ml.market.price_bars.service import (
    SelectedPriceBarsService,
    _needs_full_reconciliation,
)

_TARGET = date(2026, 7, 10)
_BACKFILL = date(2015, 1, 1)
_SOURCE = "tiingo_eod"


def _sec(ticker: str = "AAPL") -> SelectedPriceSecurity:
    return SelectedPriceSecurity(
        security_id=uuid4(), company_id=uuid4(), ticker=ticker, exchange="NASDAQ"
    )


def _bar(trading_date: date, *, close: Decimal = Decimal("150"), adj_close: Decimal | None = None) -> DailyBar:
    ac = adj_close if adj_close is not None else close
    return DailyBar(
        trading_date=trading_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000000"),
        adjusted_open=ac,
        adjusted_high=ac,
        adjusted_low=ac,
        adjusted_close=ac,
        adjusted_volume=Decimal("1000000"),
        dividend_cash=Decimal("0"),
        split_factor=Decimal("1"),
    )


def _bar_with_dividend(trading_date: date) -> DailyBar:
    return DailyBar(
        trading_date=trading_date,
        open=Decimal("150"),
        high=Decimal("152"),
        low=Decimal("149"),
        close=Decimal("151"),
        volume=Decimal("900000"),
        adjusted_open=Decimal("150"),
        adjusted_high=Decimal("152"),
        adjusted_low=Decimal("149"),
        adjusted_close=Decimal("151"),
        adjusted_volume=Decimal("900000"),
        dividend_cash=Decimal("0.25"),  # non-zero dividend
        split_factor=Decimal("1"),
    )


def _make_service(provider, session_factory=None):
    sf = session_factory or MagicMock()
    return SelectedPriceBarsService(price_provider=provider, session_factory=sf)


def _mock_repo(*, coverage=None, sync_states=None, upsert_counts=(0, 0, 0)):
    repo = MagicMock()
    repo.get_price_bar_coverage.return_value = coverage or {}
    repo.get_sync_states.return_value = sync_states or {}
    repo.bulk_upsert_price_bars.return_value = upsert_counts
    repo.upsert_sync_state.return_value = None
    return repo


# ── needs_full_reconciliation ──────────────────────────────────────────────────


def test_no_corporate_actions_no_reconciliation():
    bars = [_bar(date(2026, 7, i)) for i in range(1, 5)]
    from invest_ml.market.price_bars.models import SecurityPriceRequest
    req = SecurityPriceRequest(
        security_id=uuid4(), ticker="AAPL", mode="incremental",
        start_date=date(2026, 6, 26), end_date=_TARGET,
    )
    assert not _needs_full_reconciliation(
        bars=bars, request=req, sync_state=None, backfill_start_date=_BACKFILL
    )


def test_new_dividend_triggers_reconciliation():
    bars = [_bar_with_dividend(date(2026, 7, 5))]
    from invest_ml.market.price_bars.models import SecurityPriceRequest
    req = SecurityPriceRequest(
        security_id=uuid4(), ticker="AAPL", mode="incremental",
        start_date=date(2026, 6, 26), end_date=_TARGET,
    )
    assert _needs_full_reconciliation(
        bars=bars, request=req, sync_state=None, backfill_start_date=_BACKFILL
    )


def test_already_reconciled_action_does_not_trigger():
    """If last_reconciled_ca_date >= dividend date, no re-reconciliation."""
    from invest_ml.market.price_bars.models import SecurityPriceRequest, SyncStateData

    bars = [_bar_with_dividend(date(2026, 7, 5))]
    req = SecurityPriceRequest(
        security_id=uuid4(), ticker="AAPL", mode="incremental",
        start_date=date(2026, 6, 26), end_date=_TARGET,
    )
    state = SyncStateData(
        security_id=req.security_id, source=_SOURCE,
        backfill_start_date=_BACKFILL,
        latest_stored_trading_date=None, checked_through_date=None,
        last_attempted_at=None, last_succeeded_at=None, last_full_refresh_at=None,
        last_reconciled_corporate_action_date=date(2026, 7, 5),  # already reconciled
        status="succeeded", consecutive_failures=0, next_retry_at=None, last_error=None,
    )
    assert not _needs_full_reconciliation(
        bars=bars, request=req, sync_state=state, backfill_start_date=_BACKFILL
    )


# ── service.materialize ────────────────────────────────────────────────────────


class _FakeProvider:
    def __init__(self, bars_by_ticker):
        self._bars = bars_by_ticker
        self.calls = []

    def get_latest_available_date(self, *, reference_ticker):
        return _TARGET

    def get_daily_bars(self, *, ticker, start_date, end_date):
        self.calls.append((ticker, start_date, end_date))
        return self._bars.get(ticker, [])


def _make_sf_with_repo(repo):
    """Return a session_factory context manager that yields a mock session."""
    session = MagicMock()
    sf = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    sf.return_value = cm
    return sf, session, repo


def test_empty_securities_returns_empty_result():
    provider = _FakeProvider({})
    service = _make_service(provider)
    result = service.materialize(
        selected_securities=[],
        target_end_date=_TARGET,
        backfill_start_date=_BACKFILL,
        ingestion_run_id=uuid4(),
    )
    assert result.securities_requested == 0
    assert result.bars_received == 0


def test_initial_backfill_inserts_bars():
    sec = _sec("AAPL")
    bars = [_bar(date(2026, 7, i)) for i in range(1, 4)]
    provider = _FakeProvider({"AAPL": bars})
    repo = _mock_repo(upsert_counts=(3, 0, 0))

    with _patch_repo(repo):
        service = SelectedPriceBarsService(price_provider=provider, session_factory=_sf())
        result = service.materialize(
            selected_securities=[sec],
            target_end_date=_TARGET,
            backfill_start_date=_BACKFILL,
            ingestion_run_id=uuid4(),
        )

    assert result.securities_succeeded == 1
    assert result.initial_backfills == 1
    assert result.bars_received == 3


def _patch_repo(repo):
    """Context manager that swaps out PriceBarsRepository with a fixed instance."""
    import invest_ml.db.repositories.price_bars as _m

    class _Ctx:
        def __enter__(self):
            self._orig = _m.PriceBarsRepository
            _m.PriceBarsRepository = lambda s: repo
            return repo

        def __exit__(self, *a):
            _m.PriceBarsRepository = self._orig

    return _Ctx()


def _sf():
    session = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    sf = MagicMock(return_value=cm)
    return sf


def test_already_current_produces_no_ticker_request():
    sec = _sec("MSFT")
    state = SyncStateData(
        security_id=sec.security_id,
        source=_SOURCE,
        backfill_start_date=_BACKFILL,
        latest_stored_trading_date=_TARGET,
        checked_through_date=_TARGET,
        last_attempted_at=None,
        last_succeeded_at=None,
        last_full_refresh_at=None,
        last_reconciled_corporate_action_date=None,
        status="succeeded",
        consecutive_failures=0,
        next_retry_at=None,
        last_error=None,
    )
    provider = _FakeProvider({})
    repo = _mock_repo(sync_states={sec.security_id: state})

    with _patch_repo(repo):
        service = SelectedPriceBarsService(price_provider=provider, session_factory=_sf())
        result = service.materialize(
            selected_securities=[sec],
            target_end_date=_TARGET,
            backfill_start_date=_BACKFILL,
            ingestion_run_id=uuid4(),
        )

    assert result.securities_skipped == 1
    assert result.securities_requested == 0
    assert provider.calls == []


def test_404_marks_security_unsupported():
    from invest_ml.market.errors import MarketDataInstrumentNotFoundError

    sec = _sec("BADTICKER")

    class _FailProvider:
        def get_daily_bars(self, *, ticker, start_date, end_date):
            raise MarketDataInstrumentNotFoundError(f"404 for {ticker}")

    repo = _mock_repo()

    with _patch_repo(repo):
        service = SelectedPriceBarsService(price_provider=_FailProvider(), session_factory=_sf())
        result = service.materialize(
            selected_securities=[sec],
            target_end_date=_TARGET,
            backfill_start_date=_BACKFILL,
            ingestion_run_id=uuid4(),
        )

    assert result.securities_unsupported == 1
    assert result.securities_failed == 0


def test_one_failure_does_not_abort_when_below_threshold():
    from invest_ml.market.errors import MarketDataTemporaryError

    secs = [_sec(f"S{i}") for i in range(5)]
    bars_good = [_bar(date(2026, 7, 1))]

    class _MixedProvider:
        def get_daily_bars(self, *, ticker, start_date, end_date):
            if ticker == "S0":
                raise MarketDataTemporaryError("flaky")
            return bars_good

    repo = _mock_repo(upsert_counts=(1, 0, 0))

    with _patch_repo(repo):
        service = SelectedPriceBarsService(price_provider=_MixedProvider(), session_factory=_sf())
        result = service.materialize(
            selected_securities=secs,
            target_end_date=_TARGET,
            backfill_start_date=_BACKFILL,
            ingestion_run_id=uuid4(),
        )

    assert result.securities_failed == 1
    assert result.securities_succeeded == 4


def test_empty_provider_response_advances_checked_through_date():
    """Empty response for a valid incremental range is not a failure."""
    sec = _sec("HALTED")
    cov = PriceBarCoverage(
        security_id=sec.security_id,
        earliest_stored_date=_BACKFILL,
        latest_stored_date=date(2026, 7, 1),
        stored_row_count=2800,
    )

    provider = _FakeProvider({"HALTED": []})
    repo = _mock_repo(coverage={sec.security_id: cov}, upsert_counts=(0, 0, 0))

    upserted_states = []

    def _capture_upsert(**kwargs):
        upserted_states.append(kwargs)

    repo.upsert_sync_state.side_effect = _capture_upsert

    with _patch_repo(repo):
        service = SelectedPriceBarsService(price_provider=provider, session_factory=_sf())
        result = service.materialize(
            selected_securities=[sec],
            target_end_date=_TARGET,
            backfill_start_date=_BACKFILL,
            ingestion_run_id=uuid4(),
        )

    assert result.securities_succeeded == 1
    assert any(s.get("checked_through_date") == _TARGET for s in upserted_states)
