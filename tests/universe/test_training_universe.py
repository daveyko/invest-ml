"""Unit tests for TrainingUniverseConfig and TrainingUniverseEvaluator."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from invest_ml.universe.security_selector import EligibleSecurityInput
from invest_ml.universe.training import (
    TrainingCompanyInput,
    TrainingUniverseConfig,
    TrainingUniverseEvaluator,
)

_AS_OF = date(2025, 6, 1)

_CFG_DICT = {
    "name": "training_universe",
    "version": "v1",
    "candidate_universe": {"name": "candidate", "version": "v1"},
    "company_data_profile_version": "companyfacts_profile_v1",
    "market_profile_version": "market_profile_v1",
    "minimum_annual_periods": 3,
    "minimum_quarterly_periods": 0,
    "minimum_canonical_metric_coverage": 0.80,
    "minimum_price_history_years": 3,
    "minimum_median_daily_dollar_volume": 2_000_000,
    "maximum_missing_trading_day_ratio": 0.02,
    "maximum_market_profile_age_days": 45,
    "require_market_profile_status": "success",
    "require_latest_adjusted_close": True,
    "minimum_market_cap": None,
}


def _config(**overrides) -> TrainingUniverseConfig:
    return TrainingUniverseConfig.from_dict({**_CFG_DICT, **overrides})


def _security(
    *,
    company_id=None,
    median_daily_dollar_volume=Decimal("5_000_000"),
    price_history_years=Decimal("5"),
    missing_trading_day_ratio=Decimal("0.01"),
    latest_adjusted_close=Decimal("100.00"),
    status="success",
    scanned_days_ago=0,
) -> EligibleSecurityInput:
    scanned_at = datetime(2025, 5, 31, 12, 0, tzinfo=UTC)
    from datetime import timedelta
    scanned_at = scanned_at - timedelta(days=scanned_days_ago)
    return EligibleSecurityInput(
        security_id=uuid4(),
        company_id=company_id or uuid4(),
        ticker="TICK",
        exchange="Nasdaq",
        currently_observed=True,
        market_profile_version="market_profile_v1",
        market_profile_scanned_at=scanned_at,
        market_profile_status=status,
        first_price_date=date(2020, 1, 1),
        latest_price_date=date(2025, 5, 31),
        price_history_years=price_history_years,
        median_daily_dollar_volume=median_daily_dollar_volume,
        current_market_cap=Decimal("500_000_000"),
        missing_trading_day_ratio=missing_trading_day_ratio,
        latest_adjusted_close=latest_adjusted_close,
    )


def _company(
    *,
    candidate_membership_active=True,
    profile_version="companyfacts_profile_v1",
    annual_periods=5,
    quarterly_periods=20,
    canonical_metric_coverage=Decimal("0.90"),
    securities=None,
) -> TrainingCompanyInput:
    company_id = uuid4()
    if securities is None:
        securities = (_security(company_id=company_id),)
    return TrainingCompanyInput(
        company_id=company_id,
        cik="0001234567",
        legal_name="Acme Corp",
        candidate_membership_active=candidate_membership_active,
        company_data_profile_version=profile_version,
        annual_periods=annual_periods,
        quarterly_periods=quarterly_periods,
        canonical_metric_coverage=canonical_metric_coverage,
        company_data_quality_flags={},
        securities=securities,
    )


_EVAL = TrainingUniverseEvaluator()


def _eval(company, cfg=None):
    return _EVAL.evaluate(company, as_of_date=_AS_OF, config=cfg or _config())


def test_eligible_company_included():
    result = _eval(_company())
    assert result.included
    assert result.selected_security is not None


def test_not_in_candidate_excluded():
    result = _eval(_company(candidate_membership_active=False))
    assert not result.included
    assert "not_in_candidate_universe" in result.exclusion_reasons["reason_codes"]


def test_missing_data_profile_excluded():
    result = _eval(_company(profile_version=None))
    assert not result.included
    assert "missing_company_data_profile" in result.exclusion_reasons["reason_codes"]


def test_insufficient_annual_periods_excluded():
    result = _eval(_company(annual_periods=2))
    assert not result.included
    assert "insufficient_annual_periods" in result.exclusion_reasons["reason_codes"]


def test_insufficient_coverage_excluded():
    result = _eval(_company(canonical_metric_coverage=Decimal("0.70")))
    assert not result.included
    assert "insufficient_canonical_metric_coverage" in result.exclusion_reasons["reason_codes"]


def test_no_securities_excluded():
    result = _eval(_company(securities=()))
    assert not result.included
    assert "no_current_security" in result.exclusion_reasons["reason_codes"]


def test_insufficient_liquidity_excluded():
    sec = _security(median_daily_dollar_volume=Decimal("500_000"))
    result = _eval(_company(securities=(sec,)))
    assert not result.included
    assert "insufficient_liquidity" in result.exclusion_reasons["reason_codes"]


def test_insufficient_price_history_excluded():
    sec = _security(price_history_years=Decimal("1"))
    result = _eval(_company(securities=(sec,)))
    assert not result.included
    assert "insufficient_price_history" in result.exclusion_reasons["reason_codes"]


def test_excessive_missing_days_excluded():
    sec = _security(missing_trading_day_ratio=Decimal("0.05"))
    result = _eval(_company(securities=(sec,)))
    assert not result.included
    assert "excessive_missing_trading_days" in result.exclusion_reasons["reason_codes"]


def test_missing_adjusted_close_excluded_when_required():
    sec = _security(latest_adjusted_close=None, status="success")
    # The security won't be eligible because latest_adjusted_close=None is required by selector
    result = _eval(_company(securities=(sec,)))
    assert not result.included


def test_criteria_hash_is_deterministic():
    cfg1 = _config()
    cfg2 = _config()
    assert cfg1.criteria_hash() == cfg2.criteria_hash()


def test_criteria_hash_changes_with_threshold():
    cfg1 = _config(minimum_annual_periods=3)
    cfg2 = _config(minimum_annual_periods=5)
    assert cfg1.criteria_hash() != cfg2.criteria_hash()


def test_from_dict_parses_nested_candidate_universe():
    cfg = _config()
    assert cfg.candidate_universe_name == "candidate"
    assert cfg.candidate_universe_version == "v1"


def test_inclusion_reasons_populated_on_success():
    result = _eval(_company())
    assert "selected_security" in result.inclusion_reasons
    assert "market_profile" in result.inclusion_reasons
    assert "company_data_profile" in result.inclusion_reasons
