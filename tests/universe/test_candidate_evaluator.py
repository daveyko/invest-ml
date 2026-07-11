"""Unit tests for CandidateUniverseEvaluator.

All tests are pure Python — no DB, no HTTP, no filesystem.
"""

from datetime import date
from uuid import uuid4

from invest_ml.universe.candidate import CandidateUniverseEvaluator
from invest_ml.universe.config import CandidateUniverseConfig
from invest_ml.universe.models import CandidateCompanyInput, CandidateSecurity

# ── Fixtures ─────────────────────────────────────────────────────────────────

AS_OF = date(2026, 1, 11)


def _base_config(**overrides) -> CandidateUniverseConfig:
    defaults = dict(
        name="candidate",
        version="v1",
        supported_exchanges=("Nasdaq", "NYSE", "NYSE American"),
        exchange_aliases={
            "Nasdaq": "Nasdaq",
            "NASDAQ": "Nasdaq",
            "NYSE": "NYSE",
            "NYSE American": "NYSE American",
        },
        allowed_entity_types=("operating",),
        excluded_exact_entity_types=(
            "investment-manager",
            "pooled-investment-fund",
            "blank-check",
        ),
        exclude_missing_entity_type=True,
        require_current_ticker=True,
        require_company_data_profile=True,
        recent_filing_months=18,
        exclude_missing_recent_filing=True,
        excluded_sic_codes=frozenset(["6726", "6770"]),
        excluded_name_patterns=(r"\bETF\b", r"(?:acquisition|acq)\.?\s+corp"),
        always_exclude_ciks=frozenset(),
        always_include_ciks=frozenset(),
        profile_version="companyfacts_profile_v1",
    )
    defaults.update(overrides)
    return CandidateUniverseConfig(**defaults)


def _sec(ticker="ACME", exchange="Nasdaq", normalized=None, current=True) -> CandidateSecurity:
    if normalized is None:
        cfg = _base_config()
        normalized = cfg.exchange_aliases.get((exchange or "").strip())
    return CandidateSecurity(
        security_id=uuid4(),
        ticker=ticker,
        exchange=exchange,
        normalized_exchange=normalized,
        currently_observed=current,
    )


def _company(
    *,
    cik="0000123456",
    legal_name="Acme Corp",
    entity_type="operating",
    latest_filing_date=date(2025, 12, 1),
    sic_codes=("7372",),
    has_profile=True,
    securities=None,
) -> CandidateCompanyInput:
    if securities is None:
        securities = (_sec(),)
    return CandidateCompanyInput(
        company_id=uuid4(),
        cik=cik,
        legal_name=legal_name,
        entity_type=entity_type,
        latest_filing_date=latest_filing_date,
        sic_codes=sic_codes,
        has_current_data_profile=has_profile,
        securities=securities,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_included_operating_company():
    """Happy path: operating company with current Nasdaq ticker and recent filing."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company()
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is True
    assert not decision.exclusion_reasons
    assert len(decision.eligible_securities) == 1


def test_missing_profile_excluded():
    """Company without a CompanyDataProfile row is excluded."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(has_profile=False)
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "missing_company_data_profile" in decision.exclusion_reasons


def test_missing_profile_not_checked_when_not_required():
    """When require_company_data_profile=False, missing profile is not an exclusion."""
    cfg = _base_config(require_company_data_profile=False)
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(has_profile=False)
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is True
    assert "missing_company_data_profile" not in decision.exclusion_reasons


def test_missing_entity_type_excluded():
    """company.entity_type is None and exclude_missing_entity_type=True → excluded."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(entity_type=None)
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "missing_entity_type" in decision.exclusion_reasons


def test_unsupported_entity_type_excluded():
    """entity_type not in allowed or excluded lists → unsupported_entity_type reason."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    # "limited-partnership" is not in excluded_exact_entity_types for base config,
    # so it falls through to the unsupported_entity_type catch-all.
    company = _company(entity_type="limited-partnership")
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "unsupported_entity_type" in decision.exclusion_reasons
    assert "excluded_entity_type" not in decision.exclusion_reasons


def test_unknown_entity_type_gets_unsupported_reason():
    """entity_type not in excluded or allowed lists → unsupported_entity_type."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(entity_type="holding-company")
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "unsupported_entity_type" in decision.exclusion_reasons
    assert "excluded_entity_type" not in decision.exclusion_reasons


def test_excluded_exact_entity_type():
    """entity_type in excluded_exact_entity_types → excluded_entity_type (specific code)."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(entity_type="investment-manager")
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "excluded_entity_type" in decision.exclusion_reasons
    assert "unsupported_entity_type" not in decision.exclusion_reasons


def test_name_pattern_exclusion_etf():
    """Legal name matching \\bETF\\b → excluded_name_pattern."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(legal_name="Vanguard Total Market ETF")
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "excluded_name_pattern" in decision.exclusion_reasons


def test_name_pattern_exclusion_acquisition_corp():
    """Legal name matching acquisition corp pattern → excluded_name_pattern."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(legal_name="XYZ Acquisition Corp")
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "excluded_name_pattern" in decision.exclusion_reasons


def test_excluded_sic_code():
    """Company with a SIC code in excluded_sic_codes → excluded_sic."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(sic_codes=("6726",))
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "excluded_sic" in decision.exclusion_reasons


def test_stale_filing_date_excluded():
    """Filing date older than recent_filing_months ago → stale_latest_filing."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    # AS_OF = 2026-01-11, 18 months ago = 2024-07-11; filing on 2024-07-10 is stale
    company = _company(latest_filing_date=date(2024, 7, 10))
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "stale_latest_filing" in decision.exclusion_reasons


def test_filing_just_within_window_included():
    """Filing exactly at the 18-month boundary is NOT stale."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    # AS_OF = 2026-01-11; 18 months ago = 2024-07-11 — on the cutoff is fine
    company = _company(latest_filing_date=date(2024, 7, 11))
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is True


def test_missing_filing_date_excluded():
    """latest_filing_date=None and exclude_missing_recent_filing=True → excluded."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(latest_filing_date=None)
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "missing_latest_filing_date" in decision.exclusion_reasons


def test_no_current_ticker_excluded():
    """Company with no current securities → no_current_ticker."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(securities=())
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "no_current_ticker" in decision.exclusion_reasons


def test_no_supported_exchange_excluded():
    """Company with current tickers but none on a supported exchange → no_supported_exchange."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    otc_sec = CandidateSecurity(
        security_id=uuid4(),
        ticker="ACMEF",
        exchange="OTC",
        normalized_exchange=None,  # not in exchange_aliases
        currently_observed=True,
    )
    company = _company(securities=(otc_sec,))
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "no_supported_exchange" in decision.exclusion_reasons
    assert "no_current_ticker" not in decision.exclusion_reasons


def test_always_exclude_cik_hard_block():
    """CIK in always_exclude_ciks → explicit_exclusion regardless of other attributes."""
    cfg = _base_config(always_exclude_ciks=frozenset(["0000999999"]))
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(cik="0000999999")
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert decision.exclusion_reasons == ("explicit_exclusion",)


def test_always_include_overrides_exclusion():
    """CIK in always_include_ciks → included even with a stale filing date."""
    cfg = _base_config(always_include_ciks=frozenset(["0000123456"]))
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(cik="0000123456", latest_filing_date=date(2020, 1, 1))
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is True
    assert "always_include_override" in decision.inclusion_reasons


def test_always_include_waived_reasons_recorded():
    """Waived exclusion reasons are still present in exclusion_reasons for observability."""
    cfg = _base_config(always_include_ciks=frozenset(["0000123456"]))
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(cik="0000123456", latest_filing_date=date(2020, 1, 1), has_profile=False)
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is True
    # Both stale_latest_filing and missing_company_data_profile were waived
    assert "stale_latest_filing" in decision.exclusion_reasons
    assert "missing_company_data_profile" in decision.exclusion_reasons


def test_multiple_exclusion_reasons_collected():
    """Both missing profile and stale filing appear together in exclusion_reasons."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    company = _company(
        latest_filing_date=date(2020, 1, 1),
        has_profile=False,
    )
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is False
    assert "missing_company_data_profile" in decision.exclusion_reasons
    assert "stale_latest_filing" in decision.exclusion_reasons


def test_eligible_securities_only_supported_exchanges():
    """Only securities on supported exchanges appear in eligible_securities."""
    cfg = _base_config()
    ev = CandidateUniverseEvaluator(cfg)
    nasdaq_sec = _sec(ticker="ACME", exchange="Nasdaq", normalized="Nasdaq")
    otc_sec = CandidateSecurity(
        security_id=uuid4(),
        ticker="ACMEF",
        exchange="OTC",
        normalized_exchange=None,
        currently_observed=True,
    )
    company = _company(securities=(nasdaq_sec, otc_sec))
    decision = ev.evaluate(company, as_of_date=AS_OF)
    assert decision.included is True
    assert len(decision.eligible_securities) == 1
    assert decision.eligible_securities[0].ticker == "ACME"
