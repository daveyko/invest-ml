"""Domain models for selected-price-bars ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID


@dataclass(frozen=True)
class SelectedPriceSecurity:
    security_id: UUID
    company_id: UUID
    ticker: str
    exchange: str | None


@dataclass(frozen=True)
class PriceBarCoverage:
    security_id: UUID
    earliest_stored_date: date | None
    latest_stored_date: date | None
    stored_row_count: int


@dataclass(frozen=True)
class SyncStateData:
    security_id: UUID
    source: str
    backfill_start_date: date
    latest_stored_trading_date: date | None
    checked_through_date: date | None
    last_attempted_at: datetime | None
    last_succeeded_at: datetime | None
    last_full_refresh_at: datetime | None
    last_reconciled_corporate_action_date: date | None
    status: str
    consecutive_failures: int
    next_retry_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class SecurityPriceRequest:
    security_id: UUID
    ticker: str
    mode: str  # initial_backfill | incremental | full_reconciliation
    start_date: date
    end_date: date


@dataclass(frozen=True)
class PriceBarRequestPlan:
    selected_securities: int
    target_end_date: date

    securities_already_current: int
    securities_requiring_initial_backfill: int
    securities_requiring_incremental_update: int
    securities_requiring_full_reconciliation: int
    securities_retry_deferred: int
    securities_unsupported: int

    estimated_provider_requests: int
    requests: tuple[SecurityPriceRequest, ...]


@dataclass(frozen=True)
class SelectedPriceBarsResult:
    plan: PriceBarRequestPlan

    securities_requested: int
    securities_skipped: int
    securities_succeeded: int
    securities_failed: int
    securities_unsupported: int

    initial_backfills: int
    incremental_updates: int
    full_reconciliations: int

    provider_requests: int
    provider_retries: int
    provider_rate_limits: int

    bars_received: int
    rows_inserted: int
    rows_updated: int
    rows_unchanged: int
    invalid_rows: int

    earliest_bar_date: date | None
    latest_bar_date: date | None
