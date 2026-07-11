"""Stream individual filer records from the SEC bulk submissions ZIP.

Intentionally lightweight: reads one JSON member at a time without extracting
the complete archive, and never holds all members in memory simultaneously.
"""

import logging
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Primary filer record: CIK0000000001.json  (exactly 10 digits, no suffix)
_PRIMARY_RE = re.compile(r"^(?:.*/)?CIK(\d{10})\.json$", re.IGNORECASE)

# Historical filing shard: CIK0000000001-submissions-001.json
_SHARD_RE = re.compile(r"CIK\d{10}-submissions-\d+\.json$", re.IGNORECASE)

# Path traversal guard
_TRAVERSAL_RE = re.compile(r"\.\.")

_DEFAULT_MAX_MEMBER_BYTES = 50 * 1024 * 1024  # 50 MB


@dataclass(frozen=True)
class SubmissionArchiveRecord:
    """Raw bytes of one primary filer JSON member from the submissions ZIP."""

    member_name: str
    payload: bytes


@dataclass
class ArchiveStats:
    total_members: int = 0
    company_records: int = 0
    skipped_members: int = 0
    malformed_members: int = 0


class SubmissionArchiveReader:
    """Stream primary filer records from submissions.zip one at a time.

    Usage::

        reader = SubmissionArchiveReader()
        for record in reader.iter_company_records(archive_path):
            parsed = parse_submission(record.payload)
    """

    def __init__(self, max_member_bytes: int = _DEFAULT_MAX_MEMBER_BYTES) -> None:
        self._max_bytes = max_member_bytes

    def iter_company_records(
        self, archive_path: Path
    ) -> Iterator[SubmissionArchiveRecord]:
        """Yield one SubmissionArchiveRecord per primary filer JSON member.

        Skips shard files, non-JSON files, and oversized members.
        Records malformed members without aborting the iteration.
        """
        stats = ArchiveStats()

        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                stats.total_members += 1
                name = info.filename

                # Reject path traversal attempts.
                if _TRAVERSAL_RE.search(name):
                    logger.warning("Skipping member with traversal sequence: %s", name)
                    stats.skipped_members += 1
                    continue

                # Skip non-JSON members.
                if not name.lower().endswith(".json"):
                    stats.skipped_members += 1
                    continue

                # Skip shard files; they don't carry top-level company metadata.
                if _SHARD_RE.search(name):
                    stats.skipped_members += 1
                    continue

                # Accept only primary-filer filenames.
                if not _PRIMARY_RE.match(name):
                    logger.debug("Skipping non-primary member: %s", name)
                    stats.skipped_members += 1
                    continue

                # Enforce size cap before reading.
                if info.file_size > self._max_bytes:
                    logger.warning(
                        "Skipping oversized member %s (%d bytes > %d limit)",
                        name, info.file_size, self._max_bytes,
                    )
                    stats.skipped_members += 1
                    continue

                try:
                    with zf.open(info) as member_fh:
                        payload = member_fh.read()
                except Exception as exc:
                    logger.warning("Failed to read member %s: %s", name, exc)
                    stats.malformed_members += 1
                    continue

                # Validate JSON shape: must contain top-level "cik" and "name".
                if not _has_company_fields(payload):
                    logger.debug(
                        "Member %s lacks top-level 'cik'/'name'; treating as shard", name
                    )
                    stats.skipped_members += 1
                    continue

                stats.company_records += 1
                yield SubmissionArchiveRecord(member_name=name, payload=payload)

        logger.info(
            "Archive scan complete: total=%d company=%d skipped=%d malformed=%d",
            stats.total_members,
            stats.company_records,
            stats.skipped_members,
            stats.malformed_members,
        )

    def scan_stats(self, archive_path: Path) -> ArchiveStats:
        """Return statistics without yielding records (consumes the iterator)."""
        stats = ArchiveStats()
        for _ in self.iter_company_records(archive_path):
            stats.company_records += 1
        return stats


def _has_company_fields(payload: bytes) -> bool:
    """Quick check that the JSON bytes contain top-level 'cik' and 'name' keys.

    Avoids a full parse; just scans for the key strings.
    """
    try:
        import orjson

        obj = orjson.loads(payload)
    except Exception:
        try:
            import json

            obj = json.loads(payload)
        except Exception:
            return False
    return isinstance(obj, dict) and "cik" in obj and "name" in obj
