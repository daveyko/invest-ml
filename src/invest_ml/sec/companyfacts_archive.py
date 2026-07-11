"""Stream target-company records from the SEC bulk companyfacts ZIP.

Reads one JSON member at a time without extracting the complete archive.
Only members whose CIK appears in target_ciks are yielded; all others
are skipped in-flight without reading their bytes.
"""

import logging
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import orjson

logger = logging.getLogger(__name__)

# Matches CIK0000000001.json with an optional directory prefix.
_CF_MEMBER_RE = re.compile(r"^(?:.*/)?CIK(\d{10})\.json$", re.IGNORECASE)
_TRAVERSAL_RE = re.compile(r"\.\.")

_DEFAULT_MAX_MEMBER_BYTES = 50 * 1024 * 1024  # 50 MB


@dataclass(frozen=True)
class CompanyFactsArchiveRecord:
    """Raw bytes of one CompanyFacts JSON member from the archive."""

    member_name: str
    cik: str          # 10-digit zero-padded, from filename
    payload: bytes
    cik_mismatch: bool = False  # True when filename CIK ≠ payload CIK field


@dataclass
class CompanyFactsArchiveStats:
    total_members: int = 0
    targeted_found: int = 0
    skipped_not_targeted: int = 0
    skipped_other: int = 0
    malformed: int = 0
    cik_mismatches: int = 0
    duplicate_ciks: set = field(default_factory=set)
    found_ciks: set = field(default_factory=set)


class CompanyFactsArchiveReader:
    """Stream targeted CompanyFacts records from companyfacts.zip.

    Usage::

        stats = CompanyFactsArchiveStats()
        reader = CompanyFactsArchiveReader()
        for record in reader.iter_target_records(path, target_ciks, stats=stats):
            profiler.profile(record.cik, record.payload)
        missing = target_ciks - stats.found_ciks
    """

    def __init__(self, max_member_bytes: int = _DEFAULT_MAX_MEMBER_BYTES) -> None:
        self._max_bytes = max_member_bytes

    def iter_target_records(
        self,
        archive_path: Path,
        target_ciks: set,
        stats: CompanyFactsArchiveStats | None = None,
    ) -> Iterator[CompanyFactsArchiveRecord]:
        """Yield one CompanyFactsArchiveRecord per targeted CIK found in the archive.

        Silently skips members not in target_ciks, non-JSON files, oversized
        members, and path-traversal attempts.  Flags payload-CIK mismatches
        rather than dropping the record.  Duplicate CIK members are skipped
        after the first.
        """
        if stats is None:
            stats = CompanyFactsArchiveStats()

        seen_ciks: set = set()

        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                stats.total_members += 1
                name = info.filename

                if _TRAVERSAL_RE.search(name):
                    logger.warning("Skipping member with traversal sequence: %s", name)
                    stats.skipped_other += 1
                    continue

                if not name.lower().endswith(".json"):
                    stats.skipped_other += 1
                    continue

                m = _CF_MEMBER_RE.match(name)
                if m is None:
                    logger.debug("Skipping non-CIK member: %s", name)
                    stats.skipped_other += 1
                    continue

                cik = m.group(1)  # already 10 digits from regex

                if cik not in target_ciks:
                    stats.skipped_not_targeted += 1
                    continue

                if cik in seen_ciks:
                    logger.warning("Duplicate CIK %s in archive; skipping second member %s", cik, name)
                    stats.duplicate_ciks.add(cik)
                    stats.skipped_other += 1
                    continue

                if info.file_size > self._max_bytes:
                    logger.warning(
                        "Skipping oversized member %s (%d bytes > %d limit)",
                        name, info.file_size, self._max_bytes,
                    )
                    stats.skipped_other += 1
                    continue

                try:
                    with zf.open(info) as fh:
                        payload = fh.read()
                except Exception as exc:
                    logger.warning("Failed to read member %s: %s", name, exc)
                    stats.malformed += 1
                    continue

                cik_mismatch = _check_payload_cik(payload, cik, name)
                if cik_mismatch:
                    stats.cik_mismatches += 1

                seen_ciks.add(cik)
                stats.found_ciks.add(cik)
                stats.targeted_found += 1

                yield CompanyFactsArchiveRecord(
                    member_name=name,
                    cik=cik,
                    payload=payload,
                    cik_mismatch=cik_mismatch,
                )

        logger.info(
            "CompanyFacts archive scan complete: total=%d targeted=%d "
            "skipped_not_targeted=%d skipped_other=%d malformed=%d mismatches=%d",
            stats.total_members,
            stats.targeted_found,
            stats.skipped_not_targeted,
            stats.skipped_other,
            stats.malformed,
            stats.cik_mismatches,
        )


def _check_payload_cik(payload: bytes, expected_cik: str, member_name: str) -> bool:
    """Return True if the payload's cik field disagrees with the filename CIK."""
    try:
        obj = orjson.loads(payload)
        raw = obj.get("cik", "")
        digits = "".join(c for c in str(raw) if c.isdigit())
        payload_cik = digits.zfill(10) if digits else ""
        if payload_cik and payload_cik != expected_cik:
            logger.warning(
                "CIK mismatch in %s: filename=%s payload=%s",
                member_name, expected_cik, payload_cik,
            )
            return True
    except Exception:
        pass
    return False
