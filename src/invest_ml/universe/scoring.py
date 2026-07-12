"""Scoring universe domain types and evaluation logic.

Pure Python — no database access, no network, no side effects.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

# ── SIC bucket config ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SicBucketConfig:
    """Parsed sic_buckets_v1.yaml — maps bucket names to their SIC code sets."""

    buckets: dict[str, frozenset[str]]

    @classmethod
    def from_dict(cls, raw: dict) -> SicBucketConfig:
        model_buckets = raw.get("model_buckets", {})
        return cls(
            buckets={
                name: frozenset(str(s) for s in bucket.get("sic_codes", []))
                for name, bucket in model_buckets.items()
            }
        )

    def config_hash(self) -> str:
        serialized = {
            name: sorted(codes) for name, codes in sorted(self.buckets.items())
        }
        return hashlib.sha256(
            json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def find_matching_buckets(self, sic_codes: tuple[str, ...]) -> list[str]:
        sic_set = set(sic_codes)
        return sorted(name for name, codes in self.buckets.items() if sic_set & codes)


# ── Scoring universe config ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoringUniverseConfig:
    name: str
    version: str
    training_universe_name: str
    training_universe_version: str
    included_model_buckets: frozenset[str]
    manual_include_ciks: frozenset[str]
    manual_include_tickers: frozenset[str]
    manual_exclude_ciks: frozenset[str]
    manual_exclude_tickers: frozenset[str]

    @classmethod
    def from_dict(cls, raw: dict) -> ScoringUniverseConfig:
        training = raw.get("training_universe", {})
        return cls(
            name=raw.get("name", "scoring_universe"),
            version=str(raw.get("version", "v1")),
            training_universe_name=training.get("name", "training_universe"),
            training_universe_version=str(training.get("version", "v1")),
            included_model_buckets=frozenset(raw.get("included_model_buckets", [])),
            manual_include_ciks=frozenset(str(c) for c in raw.get("manual_include_ciks", [])),
            manual_include_tickers=frozenset(
                str(t) for t in raw.get("manual_include_tickers", [])
            ),
            manual_exclude_ciks=frozenset(str(c) for c in raw.get("manual_exclude_ciks", [])),
            manual_exclude_tickers=frozenset(
                str(t) for t in raw.get("manual_exclude_tickers", [])
            ),
        )

    def criteria_hash(self, sic_bucket_hash: str) -> str:
        relevant = {
            "training_universe_name": self.training_universe_name,
            "training_universe_version": self.training_universe_version,
            "included_model_buckets": sorted(self.included_model_buckets),
            "sic_bucket_hash": sic_bucket_hash,
            "manual_include_ciks": sorted(self.manual_include_ciks),
            "manual_include_tickers": sorted(self.manual_include_tickers),
            "manual_exclude_ciks": sorted(self.manual_exclude_ciks),
            "manual_exclude_tickers": sorted(self.manual_exclude_tickers),
        }
        return hashlib.sha256(
            json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_criteria_dict(self, sic_bucket_hash: str) -> dict:
        h = self.criteria_hash(sic_bucket_hash)
        return {
            "name": self.name,
            "version": self.version,
            "training_universe_name": self.training_universe_name,
            "training_universe_version": self.training_universe_version,
            "included_model_buckets": sorted(self.included_model_buckets),
            "sic_bucket_hash": sic_bucket_hash,
            "manual_include_ciks": sorted(self.manual_include_ciks),
            "manual_include_tickers": sorted(self.manual_include_tickers),
            "manual_exclude_ciks": sorted(self.manual_exclude_ciks),
            "manual_exclude_tickers": sorted(self.manual_exclude_tickers),
            "criteria_hash": h,
        }


# ── Input model ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoringCompanyInput:
    company_id: UUID
    security_id: UUID
    cik: str
    ticker: str
    legal_name: str
    active_sic_codes: tuple[str, ...]
    training_inclusion_reasons: Mapping[str, Any]


# ── Decision and result ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoringUniverseDecision:
    company_id: UUID
    security_id: UUID
    included: bool
    inclusion_reasons: dict
    exclusion_reasons: dict


@dataclass(frozen=True)
class ScoringUniverseResult:
    evaluated_training_members: int
    included_companies: int
    bucket_inclusions: int
    manual_inclusions: int
    newly_included: int
    already_included: int
    newly_excluded: int
    bucket_counts: Mapping[str, int]
    exclusion_counts: Mapping[str, int]
    universe_id: UUID
    criteria_hash: str


# ── Evaluator ─────────────────────────────────────────────────────────────────


class ScoringUniverseEvaluator:
    """Evaluate a single training-universe member's eligibility for scoring.

    Pure domain logic — no state that varies between calls, no side effects.

    Inclusion rules (in priority order):
      1. Manual exclusion via CIK or ticker → always excluded (hard block).
      2. SIC-bucket match on any ``included_model_buckets`` entry → included.
      3. Manual inclusion via CIK or ticker → included (bypasses bucket check only).
      4. Otherwise → excluded.

    Callers must call ``validate_manual_tickers`` BEFORE evaluating individual
    companies to surface ambiguous ticker configurations early.
    """

    def validate_manual_tickers(
        self,
        training_members: list[ScoringCompanyInput],
        config: ScoringUniverseConfig,
    ) -> None:
        """Raise ValueError if any manual ticker maps to multiple training members."""
        ticker_to_companies: dict[str, list[UUID]] = {}
        for member in training_members:
            ticker_to_companies.setdefault(member.ticker, []).append(member.company_id)

        all_manual_tickers = config.manual_include_tickers | config.manual_exclude_tickers
        for ticker in all_manual_tickers:
            matching = ticker_to_companies.get(ticker, [])
            if len(matching) > 1:
                raise ValueError(
                    f"Manual ticker {ticker!r} is ambiguous — matched {len(matching)} "
                    "training-universe members. Use CIK instead."
                )

    def evaluate(
        self,
        company: ScoringCompanyInput,
        *,
        config: ScoringUniverseConfig,
        sic_buckets: SicBucketConfig,
    ) -> ScoringUniverseDecision:
        # 1. Manual exclusion (hard block — wins over everything)
        if company.cik in config.manual_exclude_ciks:
            return self._excluded(
                company,
                ["manual_exclusion"],
                {
                    "manual_exclusion": True,
                    "manual_exclusion_source": "cik",
                    "manual_exclusion_value": company.cik,
                },
            )
        if company.ticker in config.manual_exclude_tickers:
            return self._excluded(
                company,
                ["manual_exclusion"],
                {
                    "manual_exclusion": True,
                    "manual_exclusion_source": "ticker",
                    "manual_exclusion_value": company.ticker,
                },
            )

        # 2. SIC-bucket match
        all_matched_buckets = sic_buckets.find_matching_buckets(company.active_sic_codes)
        configured_matched = [b for b in all_matched_buckets if b in config.included_model_buckets]

        if configured_matched:
            matched_sic_codes = [
                code
                for code in company.active_sic_codes
                if any(
                    code in sic_buckets.buckets.get(b, frozenset())
                    for b in configured_matched
                )
            ]
            return ScoringUniverseDecision(
                company_id=company.company_id,
                security_id=company.security_id,
                included=True,
                inclusion_reasons={
                    "matched_model_buckets": configured_matched,
                    "matched_sic_codes": matched_sic_codes,
                    "manual_inclusion": False,
                    "selected_security": {
                        "ticker": company.ticker,
                        "security_id": str(company.security_id),
                    },
                },
                exclusion_reasons={},
            )

        # 3. Manual inclusion (bypasses bucket requirement; data-quality already passed)
        if company.cik in config.manual_include_ciks:
            return self._included_manual(company, "cik", company.cik)
        if company.ticker in config.manual_include_tickers:
            return self._included_manual(company, "ticker", company.ticker)

        # 4. No match
        return self._excluded(
            company,
            ["no_matching_model_bucket"],
            {
                "all_matched_buckets": all_matched_buckets,
                "active_sic_codes": list(company.active_sic_codes),
                "manual_inclusion": False,
            },
        )

    def _included_manual(
        self,
        company: ScoringCompanyInput,
        source: str,
        value: str,
    ) -> ScoringUniverseDecision:
        return ScoringUniverseDecision(
            company_id=company.company_id,
            security_id=company.security_id,
            included=True,
            inclusion_reasons={
                "matched_model_buckets": [],
                "matched_sic_codes": [],
                "manual_inclusion": True,
                "manual_inclusion_source": source,
                "manual_inclusion_value": value,
                "selected_security": {
                    "ticker": company.ticker,
                    "security_id": str(company.security_id),
                },
            },
            exclusion_reasons={},
        )

    def _excluded(
        self,
        company: ScoringCompanyInput,
        reason_codes: list[str],
        details: dict,
    ) -> ScoringUniverseDecision:
        return ScoringUniverseDecision(
            company_id=company.company_id,
            security_id=company.security_id,
            included=False,
            inclusion_reasons={},
            exclusion_reasons={"reason_codes": reason_codes, "details": details},
        )
