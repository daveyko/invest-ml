"""Repository for price_bars and price_bar_sync_state reads and writes."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from invest_ml.db.models.company import Security
from invest_ml.db.models.ingestion import IngestionRun
from invest_ml.db.models.market import PriceBar, PriceBarSyncState
from invest_ml.db.models.universe import UniverseDefinition, UniverseMembership
from invest_ml.market.models import DailyBar
from invest_ml.market.price_bars.models import (
    PriceBarCoverage,
    SelectedPriceSecurity,
    SyncStateData,
)

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class PriceBarsRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    # ── Security selection ──────────────────────────────────────────────────

    def list_selected_training_securities(
        self,
        *,
        universe_name: str,
        universe_version: str,
        as_of_date: date,
    ) -> Sequence[SelectedPriceSecurity]:
        """Return one selected security per active training-universe company.

        Uses the security_id already persisted by the training_universe asset.
        Deduplicates by security_id and sorts by (ticker, security_id) for
        deterministic ordering.
        """
        rows = self._s.execute(
            select(
                UniverseMembership.security_id,
                UniverseMembership.company_id,
                Security.ticker,
                Security.exchange,
            )
            .join(
                UniverseDefinition,
                UniverseDefinition.universe_id == UniverseMembership.universe_id,
            )
            .join(Security, Security.security_id == UniverseMembership.security_id)
            .where(
                UniverseDefinition.name == universe_name,
                UniverseDefinition.version == universe_version,
                UniverseMembership.security_id.is_not(None),
                UniverseMembership.included_from <= as_of_date,
                (UniverseMembership.included_until.is_(None))
                | (UniverseMembership.included_until > as_of_date),
            )
            .distinct()
            .order_by(Security.ticker, UniverseMembership.security_id)
        ).all()

        seen: set[UUID] = set()
        result: list[SelectedPriceSecurity] = []
        for row in rows:
            sid = row.security_id
            if sid in seen:
                continue
            seen.add(sid)
            result.append(
                SelectedPriceSecurity(
                    security_id=sid,
                    company_id=row.company_id,
                    ticker=row.ticker,
                    exchange=row.exchange,
                )
            )
        return result

    # ── Coverage ────────────────────────────────────────────────────────────

    def get_price_bar_coverage(
        self,
        *,
        security_ids: Sequence[UUID],
        source: str,
    ) -> Mapping[UUID, PriceBarCoverage]:
        """Return MIN/MAX/COUNT coverage for all selected securities in one query."""
        if not security_ids:
            return {}

        rows = self._s.execute(
            select(
                PriceBar.security_id,
                func.min(PriceBar.trading_date).label("earliest"),
                func.max(PriceBar.trading_date).label("latest"),
                func.count().label("cnt"),
            )
            .where(
                PriceBar.security_id.in_(security_ids),
                PriceBar.source == source,
            )
            .group_by(PriceBar.security_id)
        ).all()

        return {
            row.security_id: PriceBarCoverage(
                security_id=row.security_id,
                earliest_stored_date=row.earliest,
                latest_stored_date=row.latest,
                stored_row_count=row.cnt,
            )
            for row in rows
        }

    # ── Sync state ──────────────────────────────────────────────────────────

    def get_sync_states(
        self,
        *,
        security_ids: Sequence[UUID],
        source: str,
    ) -> Mapping[UUID, SyncStateData]:
        """Return sync state for all selected securities in one query."""
        if not security_ids:
            return {}

        rows = self._s.execute(
            select(PriceBarSyncState).where(
                PriceBarSyncState.security_id.in_(security_ids),
                PriceBarSyncState.source == source,
            )
        ).scalars().all()

        return {
            row.security_id: SyncStateData(
                security_id=row.security_id,
                source=row.source,
                backfill_start_date=row.backfill_start_date,
                latest_stored_trading_date=row.latest_stored_trading_date,
                checked_through_date=row.checked_through_date,
                last_attempted_at=row.last_attempted_at,
                last_succeeded_at=row.last_succeeded_at,
                last_full_refresh_at=row.last_full_refresh_at,
                last_reconciled_corporate_action_date=row.last_reconciled_corporate_action_date,
                status=row.status,
                consecutive_failures=row.consecutive_failures,
                next_retry_at=row.next_retry_at,
                last_error=row.last_error,
            )
            for row in rows
        }

    def upsert_sync_state(
        self,
        *,
        security_id: UUID,
        source: str,
        status: str,
        backfill_start_date: date,
        checked_through_date: date | None,
        latest_stored_trading_date: date | None,
        last_attempted_at: datetime | None,
        last_succeeded_at: datetime | None,
        last_full_refresh_at: datetime | None,
        last_reconciled_corporate_action_date: date | None,
        consecutive_failures: int,
        next_retry_at: datetime | None,
        last_error: str | None,
    ) -> None:
        self._s.execute(
            pg_insert(PriceBarSyncState)
            .values(
                security_id=security_id,
                source=source,
                status=status,
                backfill_start_date=backfill_start_date,
                checked_through_date=checked_through_date,
                latest_stored_trading_date=latest_stored_trading_date,
                last_attempted_at=last_attempted_at,
                last_succeeded_at=last_succeeded_at,
                last_full_refresh_at=last_full_refresh_at,
                last_reconciled_corporate_action_date=last_reconciled_corporate_action_date,
                consecutive_failures=consecutive_failures,
                next_retry_at=next_retry_at,
                last_error=last_error,
            )
            .on_conflict_do_update(
                index_elements=["security_id", "source"],
                set_={
                    "status": pg_insert(PriceBarSyncState).excluded.status,
                    "backfill_start_date": pg_insert(PriceBarSyncState).excluded.backfill_start_date,
                    "checked_through_date": pg_insert(PriceBarSyncState).excluded.checked_through_date,
                    "latest_stored_trading_date": pg_insert(PriceBarSyncState).excluded.latest_stored_trading_date,
                    "last_attempted_at": pg_insert(PriceBarSyncState).excluded.last_attempted_at,
                    "last_succeeded_at": pg_insert(PriceBarSyncState).excluded.last_succeeded_at,
                    "last_full_refresh_at": pg_insert(PriceBarSyncState).excluded.last_full_refresh_at,
                    "last_reconciled_corporate_action_date": pg_insert(PriceBarSyncState).excluded.last_reconciled_corporate_action_date,
                    "consecutive_failures": pg_insert(PriceBarSyncState).excluded.consecutive_failures,
                    "next_retry_at": pg_insert(PriceBarSyncState).excluded.next_retry_at,
                    "last_error": pg_insert(PriceBarSyncState).excluded.last_error,
                },
            )
        )

    # ── Price bars ──────────────────────────────────────────────────────────

    def bulk_upsert_price_bars(
        self,
        *,
        security_id: UUID,
        source: str,
        source_ticker: str,
        bars: Sequence[DailyBar],
        ingested_at: datetime,
        batch_size: int = 10_000,
    ) -> tuple[int, int, int]:
        """Upsert price bars for one security.

        Returns (rows_inserted, rows_updated, rows_unchanged).

        Pre-fetches existing trading dates so we can classify bars as new vs
        existing. The upsert updates price values when they change and preserves
        ingested_at (first_ingested_at semantics).
        """
        if not bars:
            return 0, 0, 0

        existing_dates: set[date] = {
            row[0]
            for row in self._s.execute(
                select(PriceBar.trading_date).where(
                    PriceBar.security_id == security_id,
                    PriceBar.source == source,
                )
            )
        }

        new_bars = [b for b in bars if b.trading_date not in existing_dates]
        overlap_bars = [b for b in bars if b.trading_date in existing_dates]

        rows_inserted = len(new_bars)
        rows_updated = 0
        rows_unchanged = 0

        # For overlap bars we can't cheaply compare values without fetching them,
        # so we upsert all and count them as "updated" conservatively.
        rows_updated = len(overlap_bars)

        all_to_upsert = bars
        if not all_to_upsert:
            return 0, 0, 0

        for i in range(0, len(all_to_upsert), batch_size):
            chunk = all_to_upsert[i : i + batch_size]
            self._s.execute(
                pg_insert(PriceBar)
                .on_conflict_do_update(
                    index_elements=["security_id", "trading_date", "source"],
                    set_={
                        "open": pg_insert(PriceBar).excluded.open,
                        "high": pg_insert(PriceBar).excluded.high,
                        "low": pg_insert(PriceBar).excluded.low,
                        "close": pg_insert(PriceBar).excluded.close,
                        "volume": pg_insert(PriceBar).excluded.volume,
                        "adjusted_open": pg_insert(PriceBar).excluded.adjusted_open,
                        "adjusted_high": pg_insert(PriceBar).excluded.adjusted_high,
                        "adjusted_low": pg_insert(PriceBar).excluded.adjusted_low,
                        "adjusted_close": pg_insert(PriceBar).excluded.adjusted_close,
                        "adjusted_volume": pg_insert(PriceBar).excluded.adjusted_volume,
                        "dividend_cash": pg_insert(PriceBar).excluded.dividend_cash,
                        "split_factor": pg_insert(PriceBar).excluded.split_factor,
                        "source_ticker": pg_insert(PriceBar).excluded.source_ticker,
                        "last_refreshed_at": pg_insert(PriceBar).excluded.last_refreshed_at,
                        "quality_flags": pg_insert(PriceBar).excluded.quality_flags,
                        # ingested_at (first_ingested_at) intentionally excluded
                    },
                ),
                [_to_row(b, security_id, source, source_ticker, ingested_at) for b in chunk],
            )

        return rows_inserted, rows_updated, rows_unchanged

    # ── Ingestion runs ──────────────────────────────────────────────────────

    def create_ingestion_run(
        self, *, source: str, source_uri: str, started_at: datetime
    ) -> IngestionRun:
        run = IngestionRun(
            source=source,
            source_uri=source_uri,
            started_at=started_at,
            status="running",
        )
        self._s.add(run)
        self._s.flush()
        return run

    def succeed_ingestion_run(
        self,
        run_id: UUID,
        *,
        entities_checked: int,
        entities_changed: int,
        metadata: dict,
    ) -> None:
        run = self._s.get(IngestionRun, run_id)
        if run is None:
            raise ValueError(f"IngestionRun {run_id} not found")
        run.status = "succeeded"
        run.completed_at = datetime.now(tz=UTC)
        run.entities_checked = entities_checked
        run.entities_changed = entities_changed
        run.run_metadata = metadata

    def fail_ingestion_run(self, run_id: UUID, *, error: str) -> None:
        run = self._s.get(IngestionRun, run_id)
        if run is None:
            raise ValueError(f"IngestionRun {run_id} not found")
        run.status = "failed"
        run.completed_at = datetime.now(tz=UTC)
        run.error = error[:2000]


def _to_row(
    bar: DailyBar,
    security_id: UUID,
    source: str,
    source_ticker: str,
    ingested_at: datetime,
) -> dict:
    adj_close = bar.adjusted_close if bar.adjusted_close is not None else bar.close
    return {
        "security_id": security_id,
        "trading_date": bar.trading_date,
        "source": source,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "adjusted_open": bar.adjusted_open,
        "adjusted_high": bar.adjusted_high,
        "adjusted_low": bar.adjusted_low,
        "adjusted_close": adj_close,
        "adjusted_volume": int(bar.adjusted_volume) if bar.adjusted_volume is not None else None,
        "dividend_cash": bar.dividend_cash,
        "split_factor": bar.split_factor,
        "source_ticker": source_ticker,
        "ingested_at": ingested_at,
        "last_refreshed_at": ingested_at,
        "quality_flags": {},
        "market_cap": None,
    }
