"""Parse and normalize SEC submissions JSON records.

Produces CatalogCompany / CatalogSecurity domain objects that are independent
of any ORM model.  The parser is tolerant: missing optional fields never cause
a hard failure.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import orjson
from pydantic import BaseModel, ConfigDict, field_validator

logger = logging.getLogger(__name__)

# ── Raw Pydantic model (tolerant, maps directly to SEC JSON fields) ───────────


class _FilingsRecent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filingDate: list[str] = []


class _Filings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    recent: _FilingsRecent = _FilingsRecent()


class _SecSubmissionRecord(BaseModel):
    """Tolerant model of a top-level submissions JSON file.

    All fields except cik and name are optional; unknown fields are ignored.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    cik: str
    name: str
    entityType: str | None = None
    sic: str | None = None
    sicDescription: str | None = None
    tickers: list[str | None] = []
    exchanges: list[str | None] = []
    fiscalYearEnd: str | None = None
    stateOfIncorporation: str | None = None
    category: str | None = None
    filings: _Filings = _Filings()

    @field_validator("tickers", "exchanges", mode="before")
    @classmethod
    def _coerce_list(cls, v: Any) -> list:
        if v is None:
            return []
        if not isinstance(v, list):
            return [v]
        return v


# ── Normalized domain models ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CatalogSecurity:
    ticker: str
    exchange: str | None


@dataclass(frozen=True)
class CatalogCompany:
    cik: str  # always 10-digit zero-padded string
    legal_name: str
    entity_type: str | None
    sic: str | None  # 4-digit string or None; stored in company_classifications
    sic_description: str | None
    fiscal_year_end: str | None
    state_of_incorporation: str | None
    filer_category: str | None
    latest_filing_date: date | None
    securities: tuple[CatalogSecurity, ...]


@dataclass
class ParseResult:
    company: CatalogCompany | None
    warnings: list[str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.company is not None


# ── Parser ───────────────────────────────────────────────────────────────────


class SubmissionCompanyParser:
    """Parse and normalize one raw submissions JSON record."""

    def parse(self, payload: bytes, member_name: str = "") -> ParseResult:
        warnings: list[str] = []

        # 1. Decode JSON.
        try:
            raw: dict = orjson.loads(payload)
        except Exception as exc:
            return ParseResult(
                company=None,
                warnings=warnings,
                error=f"JSON decode error in {member_name!r}: {exc}",
            )

        # 2. Validate with Pydantic.
        try:
            rec = _SecSubmissionRecord.model_validate(raw)
        except Exception as exc:
            return ParseResult(
                company=None,
                warnings=warnings,
                error=f"Schema validation error in {member_name!r}: {exc}",
            )

        # 3. Normalize CIK.
        cik = _normalize_cik(rec.cik)
        if cik is None:
            return ParseResult(
                company=None,
                warnings=warnings,
                error=f"Invalid CIK {rec.cik!r} in {member_name!r}",
            )

        # 4. Normalize name.
        legal_name = (rec.name or "").strip()
        if not legal_name:
            return ParseResult(
                company=None,
                warnings=warnings,
                error=f"Empty name for CIK {cik} in {member_name!r}",
            )

        # 5. Normalize SIC.
        sic = _normalize_sic(rec.sic)
        sic_description = _blank_to_none(rec.sicDescription)

        # 6. Normalize fiscal year end.
        fiscal_year_end = _normalize_fiscal_year_end(rec.fiscalYearEnd)

        # 7. State of incorporation.
        state_of_incorporation = _blank_to_none(rec.stateOfIncorporation)

        # 8. Filer category.
        filer_category = _blank_to_none(rec.category)

        # 9. Entity type.
        entity_type = _blank_to_none(rec.entityType)

        # 10. Latest filing date.
        latest_filing_date = _max_filing_date(rec.filings.recent.filingDate, warnings, cik)

        # 11. Securities: pair tickers and exchanges by position.
        tickers = [t.strip() for t in rec.tickers if t and t.strip()]
        exchanges = rec.exchanges

        n_tickers = len(tickers)
        n_exchanges = len(exchanges)
        if n_tickers != n_exchanges:
            warnings.append(
                f"CIK {cik}: tickers ({n_tickers}) and exchanges ({n_exchanges}) "
                f"array lengths differ; using exchange=None for unmatched tickers."
            )

        seen: set[tuple[str, str | None]] = set()
        securities: list[CatalogSecurity] = []
        for i, ticker in enumerate(tickers):
            normalized_ticker = _normalize_ticker(ticker)
            if not normalized_ticker:
                warnings.append(f"CIK {cik}: blank ticker at index {i}; skipped.")
                continue
            raw_exchange = exchanges[i] if i < n_exchanges else None
            exchange = _blank_to_none(raw_exchange)
            key = (normalized_ticker, exchange)
            if key in seen:
                warnings.append(
                    f"CIK {cik}: duplicate ticker/exchange pair {key}; skipped."
                )
                continue
            seen.add(key)
            securities.append(CatalogSecurity(ticker=normalized_ticker, exchange=exchange))

        company = CatalogCompany(
            cik=cik,
            legal_name=legal_name,
            entity_type=entity_type,
            sic=sic,
            sic_description=sic_description,
            fiscal_year_end=fiscal_year_end,
            state_of_incorporation=state_of_incorporation,
            filer_category=filer_category,
            latest_filing_date=latest_filing_date,
            securities=tuple(securities),
        )
        return ParseResult(company=company, warnings=warnings)


# ── Normalization helpers ─────────────────────────────────────────────────────


def _normalize_cik(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())
    if not digits:
        return None
    return digits.zfill(10)


def _normalize_sic(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())
    if not digits:
        return None
    return digits.zfill(4)


def _normalize_ticker(raw: str) -> str:
    """Uppercase; preserve letters, digits, dots, hyphens (valid exchange symbols)."""
    cleaned = "".join(c for c in raw.upper() if c.isalnum() or c in (".", "-"))
    return cleaned


def _normalize_fiscal_year_end(raw: str | None) -> str | None:
    """Keep 4-char MMDD string; return None for blanks or invalid formats."""
    if not raw:
        return None
    s = raw.strip()
    if len(s) == 4 and s.isdigit():
        return s
    return None


def _blank_to_none(value: str | None) -> str | None:
    if not value:
        return None
    s = value.strip()
    return s if s else None


def _max_filing_date(dates: list[str], warnings: list[str], cik: str) -> date | None:
    """Parse the most recent date from the filingDate array."""
    best: date | None = None
    for ds in dates:
        try:
            d = date.fromisoformat(ds.strip())
            if best is None or d > best:
                best = d
        except (ValueError, AttributeError):
            warnings.append(f"CIK {cik}: unparseable filingDate {ds!r}; skipped.")
    return best
