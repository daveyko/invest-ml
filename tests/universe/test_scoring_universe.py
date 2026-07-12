"""Unit tests for ScoringUniverseConfig, SicBucketConfig, and ScoringUniverseEvaluator."""

from uuid import uuid4

import pytest

from invest_ml.universe.scoring import (
    ScoringCompanyInput,
    ScoringUniverseConfig,
    ScoringUniverseEvaluator,
    SicBucketConfig,
)

_SIC_RAW = {
    "model_buckets": {
        "semiconductors": {"sic_codes": ["3674"]},
        "software_and_data": {"sic_codes": ["7370", "7372"]},
        "fintech": {"sic_codes": ["6211"]},
    }
}

_CFG_DICT = {
    "name": "scoring_universe",
    "version": "v1",
    "training_universe": {"name": "training_universe", "version": "v1"},
    "included_model_buckets": ["semiconductors", "software_and_data"],
    "manual_include_ciks": [],
    "manual_include_tickers": [],
    "manual_exclude_ciks": [],
    "manual_exclude_tickers": [],
}


def _config(**overrides) -> ScoringUniverseConfig:
    return ScoringUniverseConfig.from_dict({**_CFG_DICT, **overrides})


def _sic_buckets() -> SicBucketConfig:
    return SicBucketConfig.from_dict(_SIC_RAW)


def _member(
    cik="0001234567",
    ticker="SEMI",
    sic_codes=("3674",),
    company_id=None,
    security_id=None,
) -> ScoringCompanyInput:
    return ScoringCompanyInput(
        company_id=company_id or uuid4(),
        security_id=security_id or uuid4(),
        cik=cik,
        ticker=ticker,
        legal_name="Chipco Inc",
        active_sic_codes=sic_codes,
        training_inclusion_reasons={},
    )


_EVAL = ScoringUniverseEvaluator()


def _eval(company, cfg=None, buckets=None):
    return _EVAL.evaluate(
        company,
        config=cfg or _config(),
        sic_buckets=buckets or _sic_buckets(),
    )


def test_sic_bucket_match_included():
    result = _eval(_member(sic_codes=("3674",)))
    assert result.included
    assert "semiconductors" in result.inclusion_reasons["matched_model_buckets"]
    assert not result.inclusion_reasons["manual_inclusion"]


def test_no_bucket_match_excluded():
    result = _eval(_member(sic_codes=("9999",)))
    assert not result.included
    assert "no_matching_model_bucket" in result.exclusion_reasons["reason_codes"]


def test_bucket_not_in_included_list_excluded():
    # fintech bucket is declared in sic_buckets but NOT in included_model_buckets
    result = _eval(_member(sic_codes=("6211",)))
    assert not result.included


def test_manual_include_ticker_overrides_bucket_requirement():
    cfg = _config(manual_include_tickers=["NOMATCH"])
    result = _eval(_member(ticker="NOMATCH", sic_codes=("9999",)), cfg=cfg)
    assert result.included
    assert result.inclusion_reasons["manual_inclusion"] is True
    assert result.inclusion_reasons["manual_inclusion_source"] == "ticker"


def test_manual_include_cik_overrides_bucket_requirement():
    cfg = _config(manual_include_ciks=["0000111111"])
    result = _eval(_member(cik="0000111111", sic_codes=("9999",)), cfg=cfg)
    assert result.included
    assert result.inclusion_reasons["manual_inclusion_source"] == "cik"


def test_manual_exclude_cik_wins_over_bucket_match():
    cfg = _config(manual_exclude_ciks=["0001234567"])
    result = _eval(_member(sic_codes=("3674",)), cfg=cfg)
    assert not result.included
    assert "manual_exclusion" in result.exclusion_reasons["reason_codes"]


def test_manual_exclude_ticker_wins_over_manual_include():
    cfg = _config(
        manual_include_tickers=["SEMI"],
        manual_exclude_tickers=["SEMI"],
    )
    result = _eval(_member(ticker="SEMI", sic_codes=("9999",)), cfg=cfg)
    assert not result.included
    assert "manual_exclusion" in result.exclusion_reasons["reason_codes"]


def test_validate_manual_tickers_raises_on_ambiguous():
    cfg = _config(manual_include_tickers=["DUP"])
    company_id1, company_id2 = uuid4(), uuid4()
    members = [
        _member(ticker="DUP", company_id=company_id1),
        _member(ticker="DUP", company_id=company_id2),
    ]
    with pytest.raises(ValueError, match="ambiguous"):
        _EVAL.validate_manual_tickers(members, cfg)


def test_validate_manual_tickers_ok_when_unique():
    cfg = _config(manual_include_tickers=["UNIQ"])
    members = [_member(ticker="UNIQ")]
    _EVAL.validate_manual_tickers(members, cfg)  # should not raise


def test_sic_bucket_config_hash_is_deterministic():
    b1 = _sic_buckets()
    b2 = _sic_buckets()
    assert b1.config_hash() == b2.config_hash()


def test_criteria_hash_includes_sic_bucket_hash():
    cfg = _config()
    b1 = _sic_buckets()
    h1 = cfg.criteria_hash(b1.config_hash())

    b2 = SicBucketConfig.from_dict(
        {"model_buckets": {"different": {"sic_codes": ["0001"]}}}
    )
    h2 = cfg.criteria_hash(b2.config_hash())
    assert h1 != h2
