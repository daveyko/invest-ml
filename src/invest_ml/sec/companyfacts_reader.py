"""Random-access reader for targeted members of the SEC companyfacts ZIP.

Builds a central-directory index once (one ZipFile open), then reads
individual member payloads on demand without holding all payloads in memory.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_CF_MEMBER_RE = re.compile(r"^(?:.*/)?CIK(\d{10})\.json$", re.IGNORECASE)
_TRAVERSAL_RE = re.compile(r"\.\.")
_DEFAULT_MAX_MEMBER_BYTES = 50 * 1024 * 1024


class SelectedCompanyFactsReader:
    """Read specific CompanyFacts members from the bulk ZIP by CIK.

    Usage::

        reader = SelectedCompanyFactsReader(archive_path)
        found = reader.list_found_ciks(frozenset(["0001234567", ...]))
        payload = reader.read_member("0001234567")
    """

    def __init__(
        self,
        archive_path: Path,
        max_member_bytes: int = _DEFAULT_MAX_MEMBER_BYTES,
    ) -> None:
        self._path = archive_path
        self._max_bytes = max_member_bytes
        self._cik_to_name: dict[str, str] | None = None
        self._cik_to_size: dict[str, int] | None = None

    def _build_index(self) -> None:
        if self._cik_to_name is not None:
            return
        cik_to_name: dict[str, str] = {}
        cik_to_size: dict[str, int] = {}
        with zipfile.ZipFile(self._path, "r") as zf:
            for info in zf.infolist():
                if _TRAVERSAL_RE.search(info.filename):
                    continue
                m = _CF_MEMBER_RE.match(info.filename)
                if m:
                    cik = m.group(1)
                    if cik not in cik_to_name:  # first occurrence wins
                        cik_to_name[cik] = info.filename
                        cik_to_size[cik] = info.file_size
        self._cik_to_name = cik_to_name
        self._cik_to_size = cik_to_size
        logger.debug("Built companyfacts reader index: %d members", len(cik_to_name))

    def list_found_ciks(self, target_ciks: frozenset) -> frozenset:
        """Return the subset of target_ciks present in the archive."""
        self._build_index()
        assert self._cik_to_name is not None
        return frozenset(self._cik_to_name.keys()) & target_ciks

    def read_member(self, cik: str) -> bytes | None:
        """Return raw bytes for the given 10-digit CIK, or None if not found.

        Raises ValueError if the member exceeds max_member_bytes.
        """
        self._build_index()
        assert self._cik_to_name is not None
        assert self._cik_to_size is not None
        member_name = self._cik_to_name.get(cik)
        if member_name is None:
            return None
        size = self._cik_to_size.get(cik, 0)
        if size > self._max_bytes:
            raise ValueError(
                f"Member CIK{cik} exceeds max size ({size} > {self._max_bytes} bytes)"
            )
        with zipfile.ZipFile(self._path, "r") as zf:
            with zf.open(member_name) as fh:
                return fh.read()
