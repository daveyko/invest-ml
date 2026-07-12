"""Deterministic security selection — one ticker per company for training/scoring membership.

Pure domain logic: no database access, no randomness, no side effects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class EligibleSecurityInput:
    security_id: UUID
    company_id: UUID
    ticker: str
    exchange: str | None

    currently_observed: bool

    market_profile_version: str | None
    market_profile_scanned_at: datetime | None
    market_profile_status: str | None

    first_price_date: date | None
    latest_price_date: date | None
    price_history_years: Decimal | None
    median_daily_dollar_volume: Decimal | None
    current_market_cap: Decimal | None
    missing_trading_day_ratio: Decimal | None
    latest_adjusted_close: Decimal | None


@dataclass(frozen=True)
class SecuritySelectionResult:
    selected_security: EligibleSecurityInput | None
    considered_security_ids: tuple[UUID, ...]
    selection_reasons: Mapping[str, Any]


class EligibleSecuritySelector:
    """Select one representative security per company.

    Priority order (highest to lowest):
      1. Highest median daily dollar volume (None ranks last)
      2. Longest price history years (None ranks last)
      3. Lowest missing trading day ratio (None ranks last)
      4. Most recent market profile scan date (None ranks last)
      5. Ticker ascending
      6. security_id ascending (UUID string)

    Only securities that pass the eligibility gate are considered.
    """

    def select(
        self,
        securities: Sequence[EligibleSecurityInput],
        *,
        profile_version: str,
        as_of_date: date,
        maximum_profile_age_days: int,
    ) -> SecuritySelectionResult:
        considered = tuple(s.security_id for s in securities)

        eligible = [
            s for s in securities
            if self._is_eligible(s, profile_version, as_of_date, maximum_profile_age_days)
        ]

        if not eligible:
            return SecuritySelectionResult(
                selected_security=None,
                considered_security_ids=considered,
                selection_reasons={"reason": "no_eligible_securities", "considered_count": len(securities)},
            )

        if len(eligible) == 1:
            sel = eligible[0]
            return SecuritySelectionResult(
                selected_security=sel,
                considered_security_ids=considered,
                selection_reasons=self._build_reasons(sel, "only_eligible_security", eligible),
            )

        sorted_eligible = sorted(eligible, key=self._sort_key)
        sel = sorted_eligible[0]
        method = self._selection_method(sorted_eligible)
        return SecuritySelectionResult(
            selected_security=sel,
            considered_security_ids=considered,
            selection_reasons=self._build_reasons(sel, method, eligible),
        )

    def _is_eligible(
        self,
        sec: EligibleSecurityInput,
        profile_version: str,
        as_of_date: date,
        maximum_profile_age_days: int,
    ) -> bool:
        if not sec.currently_observed:
            return False
        if not sec.ticker or not sec.ticker.strip():
            return False
        if sec.market_profile_version != profile_version:
            return False
        if sec.market_profile_status != "success":
            return False
        if sec.market_profile_scanned_at is None:
            return False
        scanned_date = (
            sec.market_profile_scanned_at.date()
            if hasattr(sec.market_profile_scanned_at, "date")
            else sec.market_profile_scanned_at
        )
        if (as_of_date - scanned_date).days > maximum_profile_age_days:
            return False
        if sec.latest_adjusted_close is None:
            return False
        return True

    def _sort_key(self, sec: EligibleSecurityInput):
        mdv = sec.median_daily_dollar_volume
        phy = sec.price_history_years
        mtr = sec.missing_trading_day_ratio
        scanned = sec.market_profile_scanned_at

        scanned_ts = scanned.timestamp() if scanned is not None else 0.0

        return (
            # 1. Highest MDV first (negate for ascending sort), None last
            (mdv is None, -mdv if mdv is not None else Decimal("0")),
            # 2. Longest history first (negate), None last
            (phy is None, -phy if phy is not None else Decimal("0")),
            # 3. Lowest missing ratio, None last
            (mtr is None, mtr if mtr is not None else Decimal("0")),
            # 4. Latest scan (negate timestamp), None last
            (scanned is None, -scanned_ts),
            # 5. Ticker ascending
            sec.ticker,
            # 6. security_id ascending (UUID string)
            str(sec.security_id),
        )

    def _selection_method(self, sorted_eligible: list[EligibleSecurityInput]) -> str:
        first, second = sorted_eligible[0], sorted_eligible[1]
        if first.median_daily_dollar_volume != second.median_daily_dollar_volume:
            return "highest_median_daily_dollar_volume"
        if first.price_history_years != second.price_history_years:
            return "longest_price_history"
        if first.missing_trading_day_ratio != second.missing_trading_day_ratio:
            return "lowest_missing_trading_day_ratio"
        if first.market_profile_scanned_at != second.market_profile_scanned_at:
            return "latest_market_profile_scan"
        if first.ticker != second.ticker:
            return "ticker_ascending"
        return "security_id_ascending"

    def _build_reasons(
        self,
        sel: EligibleSecurityInput,
        method: str,
        eligible: list[EligibleSecurityInput],
    ) -> dict:
        return {
            "selected_ticker": sel.ticker,
            "selection_method": method,
            "eligible_tickers": sorted(s.ticker for s in eligible),
            "eligible_count": len(eligible),
            "selected_median_daily_dollar_volume": (
                float(sel.median_daily_dollar_volume)
                if sel.median_daily_dollar_volume is not None else None
            ),
            "selected_price_history_years": (
                float(sel.price_history_years)
                if sel.price_history_years is not None else None
            ),
        }
