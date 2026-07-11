"""Unit tests for SubmissionCompanyParser.

Seven fixture cases plus normalization edge-cases.
No database or network calls.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from invest_ml.sec.submissions import (
    CatalogCompany,
    CatalogSecurity,
    ParseResult,
    SubmissionCompanyParser,
    _normalize_cik,
    _normalize_fiscal_year_end,
    _normalize_sic,
    _normalize_ticker,
)

_PARSER = SubmissionCompanyParser()


def _payload(**kw) -> bytes:
    return json.dumps(kw).encode()


# ── Case 1: Micron-like — full metadata, single ticker ───────────────────────


def test_micron_like_full_parse():
    data = {
        "cik": "0000723125",
        "entityType": "operating",
        "sic": "3674",
        "sicDescription": "Semiconductors & Related Devices",
        "name": "MICRON TECHNOLOGY INC",
        "tickers": ["MU"],
        "exchanges": ["Nasdaq"],
        "fiscalYearEnd": "0903",
        "stateOfIncorporation": "DE",
        "category": "Large accelerated filer",
        "filings": {"recent": {"filingDate": ["2024-10-01", "2023-10-02"]}},
    }
    result = _PARSER.parse(json.dumps(data).encode(), "CIK0000723125.json")

    assert result.ok
    c = result.company
    assert c.cik == "0000723125"
    assert c.legal_name == "MICRON TECHNOLOGY INC"
    assert c.entity_type == "operating"
    assert c.sic == "3674"
    assert c.sic_description == "Semiconductors & Related Devices"
    assert c.fiscal_year_end == "0903"
    assert c.state_of_incorporation == "DE"
    assert c.filer_category == "Large accelerated filer"
    assert c.latest_filing_date == date(2024, 10, 1)
    assert len(c.securities) == 1
    assert c.securities[0].ticker == "MU"
    assert c.securities[0].exchange == "Nasdaq"
    assert result.warnings == []


# ── Case 2: Alphabet-like — multiple tickers and exchanges ───────────────────


def test_alphabet_like_multiple_securities():
    data = {
        "cik": "1652044",
        "name": "Alphabet Inc.",
        "tickers": ["GOOGL", "GOOG"],
        "exchanges": ["Nasdaq", "Nasdaq"],
        "filings": {},
    }
    result = _PARSER.parse(json.dumps(data).encode())

    assert result.ok
    c = result.company
    assert len(c.securities) == 2
    tickers = {s.ticker for s in c.securities}
    assert tickers == {"GOOGL", "GOOG"}
    # Duplicate (ticker, exchange) pairs must be deduplicated.
    assert len({(s.ticker, s.exchange) for s in c.securities}) == 2


# ── Case 3: No tickers — empty securities tuple ───────────────────────────────


def test_no_ticker_produces_empty_securities():
    data = {"cik": "9999999999", "name": "Private Issuer LLC", "filings": {}}
    result = _PARSER.parse(json.dumps(data).encode())

    assert result.ok
    assert result.company.securities == ()
    assert result.warnings == []


# ── Case 4: Mismatched ticker / exchange arrays ───────────────────────────────


def test_mismatched_ticker_exchange_arrays_warns_and_fills_none():
    data = {
        "cik": "0000000042",
        "name": "Mismatch Corp",
        "tickers": ["AAA", "BBB", "CCC"],
        "exchanges": ["NYSE"],
        "filings": {},
    }
    result = _PARSER.parse(json.dumps(data).encode())

    assert result.ok
    securities = result.company.securities
    assert len(securities) == 3
    # Only the first ticker has an exchange.
    assert securities[0].exchange == "NYSE"
    assert securities[1].exchange is None
    assert securities[2].exchange is None
    assert any("differ" in w for w in result.warnings)


# ── Case 5: Missing optional metadata ────────────────────────────────────────


def test_missing_optional_fields_produce_none_values():
    data = {"cik": "0000000001", "name": "Minimal Filer"}
    result = _PARSER.parse(json.dumps(data).encode())

    assert result.ok
    c = result.company
    assert c.entity_type is None
    assert c.sic is None
    assert c.sic_description is None
    assert c.fiscal_year_end is None
    assert c.state_of_incorporation is None
    assert c.filer_category is None
    assert c.latest_filing_date is None
    assert c.securities == ()


# ── Case 6: Malformed JSON ────────────────────────────────────────────────────


def test_malformed_json_returns_error():
    result = _PARSER.parse(b"{ not json ]", member_name="CIK0000000001.json")

    assert not result.ok
    assert result.company is None
    assert result.error is not None
    assert "JSON decode" in result.error


# ── Case 7: Historical shard (lacks top-level cik) ───────────────────────────


def test_historical_shard_missing_cik_returns_error():
    data = {"name": "Shard name only", "filings": {"recent": {}}}
    result = _PARSER.parse(json.dumps(data).encode(), member_name="CIK0000000001.json")

    assert not result.ok
    assert result.error is not None


# ── Blank → None normalization ────────────────────────────────────────────────


def test_blank_string_fields_become_none():
    data = {
        "cik": "0000000001",
        "name": "Corp",
        "entityType": "",
        "stateOfIncorporation": "   ",
        "category": "",
        "sic": "",
    }
    result = _PARSER.parse(json.dumps(data).encode())

    assert result.ok
    c = result.company
    assert c.entity_type is None
    assert c.state_of_incorporation is None
    assert c.filer_category is None
    assert c.sic is None


# ── Ticker normalization and deduplication ────────────────────────────────────


def test_ticker_uppercased():
    data = {"cik": "1", "name": "X", "tickers": ["mu"], "exchanges": ["Nasdaq"]}
    result = _PARSER.parse(json.dumps(data).encode())
    assert result.ok
    assert result.company.securities[0].ticker == "MU"


def test_duplicate_ticker_exchange_pairs_deduplicated():
    data = {
        "cik": "1",
        "name": "X",
        "tickers": ["MU", "MU"],
        "exchanges": ["Nasdaq", "Nasdaq"],
    }
    result = _PARSER.parse(json.dumps(data).encode())
    assert result.ok
    assert len(result.company.securities) == 1
    assert any("duplicate" in w.lower() for w in result.warnings)


def test_null_exchange_in_array_treated_as_none():
    """SEC sends exchanges: [null, "NYSE"] in real data — must not crash."""
    data = {
        "cik": "0001672572",
        "name": "Real Filer Inc",
        "tickers": ["AAPL", "AAPL"],
        "exchanges": [None, "NYSE"],
    }
    result = _PARSER.parse(json.dumps(data).encode())
    assert result.ok
    securities = result.company.securities
    assert len(securities) == 2
    exchanges = {s.exchange for s in securities}
    assert None in exchanges
    assert "NYSE" in exchanges


def test_all_null_exchanges_parsed_without_error():
    data = {
        "cik": "0001496383",
        "name": "Null Exchange Corp",
        "tickers": ["XYZ"],
        "exchanges": [None],
    }
    result = _PARSER.parse(json.dumps(data).encode())
    assert result.ok
    assert result.company.securities[0].exchange is None


def test_blank_ticker_is_skipped_with_warning():
    data = {
        "cik": "1",
        "name": "X",
        "tickers": ["", "AAPL"],
        "exchanges": ["NYSE", "Nasdaq"],
    }
    result = _PARSER.parse(json.dumps(data).encode())
    assert result.ok
    assert len(result.company.securities) == 1
    assert result.company.securities[0].ticker == "AAPL"


# ── CIK normalization ────────────────────────────────────────────────────────


def test_short_cik_is_zero_padded():
    assert _normalize_cik("723125") == "0000723125"


def test_10_digit_cik_unchanged():
    assert _normalize_cik("0000723125") == "0000723125"


def test_cik_with_leading_zeros_string():
    assert _normalize_cik("0000000001") == "0000000001"


def test_none_cik_returns_none():
    assert _normalize_cik(None) is None


def test_non_numeric_cik_returns_none():
    assert _normalize_cik("ABCDEF") is None


# ── SIC normalization ────────────────────────────────────────────────────────


def test_short_sic_is_zero_padded():
    assert _normalize_sic("73") == "0073"


def test_4_digit_sic_unchanged():
    assert _normalize_sic("3674") == "3674"


def test_none_sic_returns_none():
    assert _normalize_sic(None) is None


# ── Fiscal year end normalization ────────────────────────────────────────────


def test_valid_4_char_fyend_accepted():
    assert _normalize_fiscal_year_end("0903") == "0903"


def test_non_4_digit_fyend_returns_none():
    assert _normalize_fiscal_year_end("09") is None
    assert _normalize_fiscal_year_end("september") is None


def test_none_fyend_returns_none():
    assert _normalize_fiscal_year_end(None) is None


# ── Ticker normalization ──────────────────────────────────────────────────────


def test_ticker_preserves_dot_and_hyphen():
    assert _normalize_ticker("brk.b") == "BRK.B"
    assert _normalize_ticker("bRk-A") == "BRK-A"


def test_ticker_removes_special_chars():
    assert _normalize_ticker("A B!C") == "ABC"


# ── Latest filing date ────────────────────────────────────────────────────────


def test_latest_filing_date_picks_most_recent():
    data = {
        "cik": "1",
        "name": "X",
        "filings": {"recent": {"filingDate": ["2023-01-15", "2024-06-01", "2022-12-31"]}},
    }
    result = _PARSER.parse(json.dumps(data).encode())
    assert result.ok
    assert result.company.latest_filing_date == date(2024, 6, 1)


def test_unparseable_filing_date_produces_warning():
    data = {
        "cik": "1",
        "name": "X",
        "filings": {"recent": {"filingDate": ["not-a-date", "2024-01-01"]}},
    }
    result = _PARSER.parse(json.dumps(data).encode())
    assert result.ok
    assert result.company.latest_filing_date == date(2024, 1, 1)
    assert any("filingDate" in w for w in result.warnings)
