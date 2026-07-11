"""Cross-cutting utilities for hashing and point-in-time invariant enforcement."""

import hashlib
import json
from datetime import date


def deterministic_hash(data: dict | list | str) -> str:
    """SHA-256 of the canonical JSON serialization of data.

    Keys in dicts are sorted so the hash is stable regardless of insertion order.
    """
    if isinstance(data, str):
        payload = data.encode()
    else:
        payload = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def xbrl_fact_id(
    company_id: str,
    taxonomy: str,
    tag: str,
    unit: str,
    period_end: date,
    filed_date: date,
    dimensions: dict,
) -> str:
    """Deterministic fact_id for an XBRL fact row.

    The same source data always produces the same ID, making ingestion
    idempotent via INSERT ... ON CONFLICT DO NOTHING.
    """
    return deterministic_hash(
        {
            "company_id": str(company_id),
            "taxonomy": taxonomy,
            "tag": tag,
            "unit": unit,
            "period_end": period_end.isoformat(),
            "filed_date": filed_date.isoformat(),
            "dimensions": dimensions,
        }
    )


def assert_no_lookahead(available_at: date, as_of_date: date) -> None:
    """Raise ValueError if available_at is after as_of_date.

    Call this before using any financial data in feature construction to
    guard against point-in-time leakage.  Labels are explicitly exempt from
    this rule because they deliberately use future prices.
    """
    if available_at > as_of_date:
        raise ValueError(
            f"Point-in-time violation: data available_at={available_at} is after "
            f"as_of_date={as_of_date}.  Feature construction must only use data "
            f"that was known on or before as_of_date."
        )


def source_lineage_hash(lineage: dict) -> str:
    """Hash over a feature snapshot's source lineage for deduplication."""
    return deterministic_hash(lineage)
