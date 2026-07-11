"""Repository for company_market_profiles: target loading and profile persistence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from invest_ml.db.models.profiling import CompanyMarketProfile

logger = logging.getLogger(__name__)

_PROFILE_VERSION = "market_profile_v1"


@dataclass(frozen=True)
class MarketProfileTarget:
    company_id: UUID
    security_id: UUID
    ticker: str
    exchange: str | None
    existing_profile_scanned_at: datetime | None
    existing_profile_status: str | None


class CompanyMarketProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_market_profile_targets(
        self,
        *,
        universe_name: str,
        universe_version: str,
        profile_version: str,
        refresh_after_days: int,
        failed_symbol_retry_after_days: int,
        maximum_symbols: int,
    ) -> list[MarketProfileTarget]:
        """Return securities that need profiling, ordered by priority.

        Priority: missing first, then oldest scanned_at.
        Filters to: missing OR stale success OR retryable temporary failure.
        """
        sql = text("""
            SELECT
                c.company_id,
                s.security_id,
                s.ticker,
                s.exchange,
                cmp.scanned_at   AS existing_scanned_at,
                (cmp.quality_flags->>'status') AS existing_status
            FROM universe_memberships um
            JOIN companies c ON c.company_id = um.company_id
            JOIN securities s ON s.company_id = c.company_id
                AND s.is_currently_reported_by_sec = TRUE
                AND s.ticker IS NOT NULL AND s.ticker <> ''
            JOIN universe_definitions ud
                ON ud.universe_id = um.universe_id
                AND ud.name = :universe_name
                AND ud.version = :universe_version
            LEFT JOIN company_market_profiles cmp
                ON cmp.security_id = s.security_id
                AND cmp.profile_version = :profile_version
            WHERE
                um.included_until IS NULL
                AND (
                    cmp.security_id IS NULL
                    OR (
                        (cmp.quality_flags->>'status') = 'success'
                        AND cmp.scanned_at < NOW() - (:refresh_after_days || ' days')::INTERVAL
                    )
                    OR (
                        (cmp.quality_flags->>'status') = 'temporary_failure'
                        AND cmp.scanned_at < NOW() - (:retry_after_days || ' days')::INTERVAL
                    )
                )
            ORDER BY
                CASE WHEN cmp.security_id IS NULL THEN 0 ELSE 1 END,
                cmp.scanned_at ASC NULLS FIRST,
                s.ticker ASC
            LIMIT :limit
        """)

        rows = self._session.execute(
            sql,
            {
                "universe_name": universe_name,
                "universe_version": universe_version,
                "profile_version": profile_version,
                "refresh_after_days": refresh_after_days,
                "retry_after_days": failed_symbol_retry_after_days,
                "limit": maximum_symbols,
            },
        ).fetchall()

        return [
            MarketProfileTarget(
                company_id=row.company_id,
                security_id=row.security_id,
                ticker=row.ticker,
                exchange=row.exchange,
                existing_profile_scanned_at=row.existing_scanned_at,
                existing_profile_status=row.existing_status,
            )
            for row in rows
        ]

    def upsert_profile(
        self,
        *,
        security_id: UUID,
        profile_version: str,
        scanned_at: datetime,
        source: str,
        first_price_date: date | None,
        latest_price_date: date | None,
        price_history_years: float | None,
        median_daily_dollar_volume: float | None,
        current_market_cap: float | None,
        missing_trading_day_ratio: float | None,
        latest_adjusted_close: float | None,
        quality_flags: dict[str, Any],
    ) -> None:
        stmt = (
            pg_insert(CompanyMarketProfile)
            .values(
                security_id=security_id,
                profile_version=profile_version,
                scanned_at=scanned_at,
                source=source,
                first_price_date=first_price_date,
                latest_price_date=latest_price_date,
                price_history_years=price_history_years,
                median_daily_dollar_volume=median_daily_dollar_volume,
                current_market_cap=current_market_cap,
                missing_trading_day_ratio=missing_trading_day_ratio,
                latest_adjusted_close=latest_adjusted_close,
                quality_flags=quality_flags,
            )
            .on_conflict_do_update(
                index_elements=["security_id", "profile_version"],
                set_={
                    "scanned_at": scanned_at,
                    "source": source,
                    "first_price_date": first_price_date,
                    "latest_price_date": latest_price_date,
                    "price_history_years": price_history_years,
                    "median_daily_dollar_volume": median_daily_dollar_volume,
                    "current_market_cap": current_market_cap,
                    "missing_trading_day_ratio": missing_trading_day_ratio,
                    "latest_adjusted_close": latest_adjusted_close,
                    "quality_flags": quality_flags,
                },
            )
        )
        self._session.execute(stmt)
