"""Training universe domain types and evaluation logic.

Pure Python — no database access, no network, no side effects.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from invest_ml.universe.security_selector import (
    EligibleSecurityInput,
    EligibleSecuritySelector,
    SecuritySelectionResult,
)

# ── Config ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrainingUniverseConfig:
    name: str
    version: str
    candidate_universe_name: str
    candidate_universe_version: str
    company_data_profile_version: str
    market_profile_version: str
    minimum_annual_periods: int
    minimum_quarterly_periods: int
    minimum_canonical_metric_coverage: Decimal
    minimum_price_history_years: Decimal
    minimum_median_daily_dollar_volume: Decimal
    maximum_missing_trading_day_ratio: Decimal
    maximum_market_profile_age_days: int
    require_market_profile_status: str
    require_latest_adjusted_close: bool
    minimum_market_cap: Decimal | None

    @classmethod
    def from_dict(cls, raw: dict) -> TrainingUniverseConfig:
        cand = raw.get("candidate_universe", {})
        return cls(
            name=raw.get("name", "training_universe"),
            version=str(raw.get("version", "v1")),
            candidate_universe_name=cand.get("name", "candidate"),
            candidate_universe_version=str(cand.get("version", "v1")),
            company_data_profile_version=raw.get(
                "company_data_profile_version", "companyfacts_profile_v1"
            ),
            market_profile_version=raw.get("market_profile_version", "market_profile_v1"),
            minimum_annual_periods=int(raw.get("minimum_annual_periods", 3)),
            minimum_quarterly_periods=int(raw.get("minimum_quarterly_periods", 0)),
            minimum_canonical_metric_coverage=Decimal(
                str(raw.get("minimum_canonical_metric_coverage", "0.80"))
            ),
            minimum_price_history_years=Decimal(
                str(raw.get("minimum_price_history_years", "3"))
            ),
            minimum_median_daily_dollar_volume=Decimal(
                str(raw.get("minimum_median_daily_dollar_volume", "2000000"))
            ),
            maximum_missing_trading_day_ratio=Decimal(
                str(raw.get("maximum_missing_trading_day_ratio", "0.02"))
            ),
            maximum_market_profile_age_days=int(raw.get("maximum_market_profile_age_days", 45)),
            require_market_profile_status=raw.get("require_market_profile_status", "success"),
            require_latest_adjusted_close=bool(raw.get("require_latest_adjusted_close", True)),
            minimum_market_cap=(
                Decimal(str(raw["minimum_market_cap"]))
                if raw.get("minimum_market_cap") is not None else None
            ),
        )

    def criteria_hash(self) -> str:
        relevant = {
            "candidate_universe_name": self.candidate_universe_name,
            "candidate_universe_version": self.candidate_universe_version,
            "company_data_profile_version": self.company_data_profile_version,
            "market_profile_version": self.market_profile_version,
            "minimum_annual_periods": self.minimum_annual_periods,
            "minimum_quarterly_periods": self.minimum_quarterly_periods,
            "minimum_canonical_metric_coverage": str(self.minimum_canonical_metric_coverage),
            "minimum_price_history_years": str(self.minimum_price_history_years),
            "minimum_median_daily_dollar_volume": str(self.minimum_median_daily_dollar_volume),
            "maximum_missing_trading_day_ratio": str(self.maximum_missing_trading_day_ratio),
            "maximum_market_profile_age_days": self.maximum_market_profile_age_days,
            "require_market_profile_status": self.require_market_profile_status,
            "require_latest_adjusted_close": self.require_latest_adjusted_close,
            "minimum_market_cap": (
                str(self.minimum_market_cap) if self.minimum_market_cap is not None else None
            ),
            "security_selection_policy": "deterministic_liquidity_priority",
        }
        return hashlib.sha256(
            json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_criteria_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "candidate_universe_name": self.candidate_universe_name,
            "candidate_universe_version": self.candidate_universe_version,
            "company_data_profile_version": self.company_data_profile_version,
            "market_profile_version": self.market_profile_version,
            "minimum_annual_periods": self.minimum_annual_periods,
            "minimum_quarterly_periods": self.minimum_quarterly_periods,
            "minimum_canonical_metric_coverage": str(self.minimum_canonical_metric_coverage),
            "minimum_price_history_years": str(self.minimum_price_history_years),
            "minimum_median_daily_dollar_volume": str(self.minimum_median_daily_dollar_volume),
            "maximum_missing_trading_day_ratio": str(self.maximum_missing_trading_day_ratio),
            "maximum_market_profile_age_days": self.maximum_market_profile_age_days,
            "require_market_profile_status": self.require_market_profile_status,
            "require_latest_adjusted_close": self.require_latest_adjusted_close,
            "minimum_market_cap": (
                str(self.minimum_market_cap) if self.minimum_market_cap is not None else None
            ),
            "security_selection_policy": "deterministic_liquidity_priority",
        }


# ── Input model ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrainingCompanyInput:
    company_id: UUID
    cik: str
    legal_name: str
    candidate_membership_active: bool
    company_data_profile_version: str | None
    annual_periods: int
    quarterly_periods: int
    canonical_metric_coverage: Decimal
    company_data_quality_flags: Mapping[str, Any]
    securities: tuple[EligibleSecurityInput, ...]


# ── Decision ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrainingUniverseDecision:
    company_id: UUID
    included: bool
    selected_security: EligibleSecurityInput | None
    security_selection: SecuritySelectionResult | None
    inclusion_reasons: dict
    exclusion_reasons: dict


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrainingUniverseResult:
    evaluated_companies: int
    included_companies: int
    newly_included: int
    already_included: int
    newly_excluded: int
    selected_security_changes: int
    exclusion_counts: Mapping[str, int]
    universe_id: UUID
    criteria_hash: str


# ── Evaluator ─────────────────────────────────────────────────────────────────


class TrainingUniverseEvaluator:
    """Evaluate a single company's eligibility for the training universe.

    Pure domain logic — called independently for every company; has no state that
    varies between calls and no side effects.
    """

    def __init__(self) -> None:
        self._selector = EligibleSecuritySelector()

    def evaluate(
        self,
        company: TrainingCompanyInput,
        *,
        as_of_date: date,
        config: TrainingUniverseConfig,
    ) -> TrainingUniverseDecision:
        reason_codes: list[str] = []
        details: dict[str, Any] = {}

        # 1. Active candidate membership required
        if not company.candidate_membership_active:
            return self._excluded(company, ["not_in_candidate_universe"], {})

        # 2. Company data profile must exist for the configured version
        if company.company_data_profile_version is None:
            return self._excluded(company, ["missing_company_data_profile"], {})

        # 3. Annual reporting periods
        if company.annual_periods < config.minimum_annual_periods:
            reason_codes.append("insufficient_annual_periods")
            details["annual_periods"] = company.annual_periods
            details["minimum_annual_periods"] = config.minimum_annual_periods

        # 4. Quarterly reporting periods
        if company.quarterly_periods < config.minimum_quarterly_periods:
            reason_codes.append("insufficient_quarterly_periods")
            details["quarterly_periods"] = company.quarterly_periods
            details["minimum_quarterly_periods"] = config.minimum_quarterly_periods

        # 5. Canonical metric coverage
        if company.canonical_metric_coverage < config.minimum_canonical_metric_coverage:
            reason_codes.append("insufficient_canonical_metric_coverage")
            details["canonical_metric_coverage"] = float(company.canonical_metric_coverage)
            details["minimum_canonical_metric_coverage"] = float(
                config.minimum_canonical_metric_coverage
            )

        # Fail fast on data-profile issues before doing expensive security selection
        if reason_codes:
            return self._excluded(company, reason_codes, details)

        # 6. Security selection (must yield an eligible security)
        if not company.securities:
            return self._excluded(company, ["no_current_security"], {})

        selection = self._selector.select(
            company.securities,
            profile_version=config.market_profile_version,
            as_of_date=as_of_date,
            maximum_profile_age_days=config.maximum_market_profile_age_days,
        )

        if selection.selected_security is None:
            return self._excluded(company, ["no_eligible_security"], {})

        sel = selection.selected_security

        # 7. Price history
        if sel.price_history_years is None or sel.price_history_years < config.minimum_price_history_years:
            reason_codes.append("insufficient_price_history")
            details["price_history_years"] = (
                float(sel.price_history_years) if sel.price_history_years is not None else None
            )
            details["minimum_price_history_years"] = float(config.minimum_price_history_years)

        # 8. Liquidity
        if (
            sel.median_daily_dollar_volume is None
            or sel.median_daily_dollar_volume < config.minimum_median_daily_dollar_volume
        ):
            reason_codes.append("insufficient_liquidity")
            details["median_daily_dollar_volume"] = (
                float(sel.median_daily_dollar_volume)
                if sel.median_daily_dollar_volume is not None else None
            )
            details["minimum_median_daily_dollar_volume"] = float(
                config.minimum_median_daily_dollar_volume
            )

        # 9. Missing trading day ratio
        if (
            sel.missing_trading_day_ratio is not None
            and sel.missing_trading_day_ratio > config.maximum_missing_trading_day_ratio
        ):
            reason_codes.append("excessive_missing_trading_days")
            details["missing_trading_day_ratio"] = float(sel.missing_trading_day_ratio)
            details["maximum_missing_trading_day_ratio"] = float(
                config.maximum_missing_trading_day_ratio
            )

        # 10. Latest adjusted close presence
        if config.require_latest_adjusted_close and sel.latest_adjusted_close is None:
            reason_codes.append("missing_latest_adjusted_close")

        # 11. Optional market cap floor
        if config.minimum_market_cap is not None:
            if (
                sel.current_market_cap is None
                or sel.current_market_cap < config.minimum_market_cap
            ):
                reason_codes.append("below_minimum_market_cap")
                details["current_market_cap"] = (
                    float(sel.current_market_cap)
                    if sel.current_market_cap is not None else None
                )
                details["minimum_market_cap"] = float(config.minimum_market_cap)

        if reason_codes:
            return self._excluded(company, reason_codes, details)

        return TrainingUniverseDecision(
            company_id=company.company_id,
            included=True,
            selected_security=sel,
            security_selection=selection,
            inclusion_reasons=self._build_inclusion_reasons(company, sel, selection, config),
            exclusion_reasons={},
        )

    def _excluded(
        self,
        company: TrainingCompanyInput,
        reason_codes: list[str],
        details: dict,
    ) -> TrainingUniverseDecision:
        return TrainingUniverseDecision(
            company_id=company.company_id,
            included=False,
            selected_security=None,
            security_selection=None,
            inclusion_reasons={},
            exclusion_reasons={"reason_codes": reason_codes, "details": details},
        )

    def _build_inclusion_reasons(
        self,
        company: TrainingCompanyInput,
        sel: EligibleSecurityInput,
        selection: SecuritySelectionResult,
        config: TrainingUniverseConfig,
    ) -> dict:
        return {
            "selected_security": {
                "security_id": str(sel.security_id),
                "ticker": sel.ticker,
                "exchange": sel.exchange,
            },
            "company_data_profile": {
                "profile_version": company.company_data_profile_version,
                "annual_periods": company.annual_periods,
                "quarterly_periods": company.quarterly_periods,
                "canonical_metric_coverage": float(company.canonical_metric_coverage),
            },
            "market_profile": {
                "profile_version": config.market_profile_version,
                "price_history_years": (
                    float(sel.price_history_years) if sel.price_history_years is not None else None
                ),
                "median_daily_dollar_volume": (
                    float(sel.median_daily_dollar_volume)
                    if sel.median_daily_dollar_volume is not None else None
                ),
                "missing_trading_day_ratio": (
                    float(sel.missing_trading_day_ratio)
                    if sel.missing_trading_day_ratio is not None else None
                ),
                "current_market_cap": (
                    float(sel.current_market_cap)
                    if sel.current_market_cap is not None else None
                ),
            },
            "security_selection": {
                "selection_method": selection.selection_reasons.get("selection_method"),
                "eligible_tickers": selection.selection_reasons.get("eligible_tickers", []),
            },
        }
