"""CandidateUniverseEvaluator: pure-Python rule engine for company inclusion.

No DB access. No HTTP. Given a CandidateCompanyInput and an as_of_date,
produces a CandidateDecision with structured reason codes for observability.

Evaluation layers (in order):
  0. always_exclude_ciks  — hard block, no override
  1. Hard requirements    — profile, current ticker, supported exchange
  2. Exclusion rules      — entity_type, name patterns, SIC, filing recency
  3. always_include_ciks  — override: include despite any collected reasons
"""

from __future__ import annotations

import re
from datetime import date

from dateutil.relativedelta import relativedelta

from invest_ml.universe.config import CandidateUniverseConfig
from invest_ml.universe.models import CandidateCompanyInput, CandidateDecision


class CandidateUniverseEvaluator:
    """Stateless; compile regex patterns once at construction."""

    def __init__(self, config: CandidateUniverseConfig) -> None:
        self._config = config
        self._compiled_patterns = [
            re.compile(pat, re.IGNORECASE) for pat in config.excluded_name_patterns
        ]
        self._supported_exchanges = frozenset(config.supported_exchanges)
        self._excluded_et = frozenset(e.lower() for e in config.excluded_exact_entity_types)
        self._allowed_et = frozenset(e.lower() for e in config.allowed_entity_types)
        self._excluded_sics = frozenset(config.excluded_sic_codes)

    def evaluate(self, company: CandidateCompanyInput, *, as_of_date: date) -> CandidateDecision:
        cfg = self._config

        # ── Layer 0: hard block ───────────────────────────────────────────────
        if company.cik in cfg.always_exclude_ciks:
            return CandidateDecision(
                company_id=company.company_id,
                included=False,
                exclusion_reasons=("explicit_exclusion",),
                inclusion_reasons=(),
                eligible_securities=(),
            )

        reasons: list[str] = []

        # ── Layer 1: hard requirements ────────────────────────────────────────
        if cfg.require_company_data_profile and not company.has_current_data_profile:
            reasons.append("missing_company_data_profile")

        current_secs = [
            s for s in company.securities if s.currently_observed and s.ticker.strip()
        ]
        eligible_secs: list = []
        if not current_secs:
            reasons.append("no_current_ticker")
        else:
            for sec in current_secs:
                if sec.normalized_exchange in self._supported_exchanges:
                    eligible_secs.append(sec)
            if not eligible_secs:
                reasons.append("no_supported_exchange")

        # ── Layer 2: exclusion rules ──────────────────────────────────────────
        et = (company.entity_type or "").strip()
        if not et:
            if cfg.exclude_missing_entity_type:
                reasons.append("missing_entity_type")
        else:
            et_lower = et.lower()
            if et_lower in self._excluded_et:
                reasons.append("excluded_entity_type")
            elif et_lower not in self._allowed_et:
                reasons.append("unsupported_entity_type")

        for pat in self._compiled_patterns:
            if pat.search(company.legal_name):
                reasons.append("excluded_name_pattern")
                break

        for sic in company.sic_codes:
            if sic in self._excluded_sics:
                reasons.append("excluded_sic")
                break

        if company.latest_filing_date is None:
            if cfg.exclude_missing_recent_filing:
                reasons.append("missing_latest_filing_date")
        else:
            cutoff = as_of_date - relativedelta(months=cfg.recent_filing_months)
            if company.latest_filing_date < cutoff:
                reasons.append("stale_latest_filing")

        # ── Layer 3: always_include override ──────────────────────────────────
        if company.cik in cfg.always_include_ciks:
            return CandidateDecision(
                company_id=company.company_id,
                included=True,
                inclusion_reasons=("always_include_override",),
                exclusion_reasons=tuple(reasons),
                eligible_securities=tuple(eligible_secs),
            )

        if reasons:
            return CandidateDecision(
                company_id=company.company_id,
                included=False,
                exclusion_reasons=tuple(reasons),
                inclusion_reasons=(),
                eligible_securities=(),
            )

        return CandidateDecision(
            company_id=company.company_id,
            included=True,
            inclusion_reasons=(),
            exclusion_reasons=(),
            eligible_securities=tuple(eligible_secs),
        )
