"""Unit tests for CompanyFactsArchiveReader.

All tests use in-memory ZIPs — no network, no real SEC archive.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from invest_ml.sec.companyfacts_archive import (
    CompanyFactsArchiveReader,
    CompanyFactsArchiveStats,
)

_MAX_BYTES = 1024 * 1024  # 1 MB for tests


def _make_zip(members: dict[str, bytes], path: Path) -> Path:
    """Write an in-memory ZIP to *path* and return it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return path


def _cf_json(cik_digits: str = "0000723125", name: str = "ACME INC") -> bytes:
    return json.dumps({
        "cik": f"CIK{cik_digits}",
        "name": name,
        "facts": {},
    }).encode()


_READER = CompanyFactsArchiveReader(max_member_bytes=_MAX_BYTES)
_TARGET = {"0000723125"}


# ── Basic yield behaviour ────────────────────────────────────────────────────


def test_targeted_record_is_yielded(tmp_path):
    path = _make_zip({"CIK0000723125.json": _cf_json()}, tmp_path / "cf.zip")
    records = list(_READER.iter_target_records(path, _TARGET))
    assert len(records) == 1
    assert records[0].cik == "0000723125"


def test_untargeted_cik_is_skipped(tmp_path):
    path = _make_zip({"CIK9999999999.json": _cf_json("9999999999", "Other")}, tmp_path / "cf.zip")
    records = list(_READER.iter_target_records(path, _TARGET))
    assert records == []


def test_multiple_targets_all_yielded(tmp_path):
    cik2 = "0000012345"
    path = _make_zip(
        {
            "CIK0000723125.json": _cf_json("0000723125"),
            f"CIK{cik2}.json": _cf_json(cik2, "Other Inc"),
        },
        tmp_path / "cf.zip",
    )
    targets = {"0000723125", cik2}
    records = list(_READER.iter_target_records(path, targets))
    assert {r.cik for r in records} == targets


# ── Duplicate detection ──────────────────────────────────────────────────────


def test_duplicate_member_skipped_after_first(tmp_path):
    path = _make_zip(
        {
            "a/CIK0000723125.json": _cf_json(),
            "b/CIK0000723125.json": _cf_json(),
        },
        tmp_path / "cf.zip",
    )
    stats = CompanyFactsArchiveStats()
    records = list(_READER.iter_target_records(path, _TARGET, stats=stats))
    assert len(records) == 1
    assert "0000723125" in stats.duplicate_ciks


# ── Security guards ──────────────────────────────────────────────────────────


def test_traversal_path_rejected(tmp_path):
    path = _make_zip({"../CIK0000723125.json": _cf_json()}, tmp_path / "cf.zip")
    stats = CompanyFactsArchiveStats()
    records = list(_READER.iter_target_records(path, _TARGET, stats=stats))
    assert records == []
    assert stats.skipped_other >= 1


def test_non_json_member_skipped(tmp_path):
    path = _make_zip({"CIK0000723125.txt": b"plain text"}, tmp_path / "cf.zip")
    records = list(_READER.iter_target_records(path, _TARGET))
    assert records == []


def test_non_cik_filename_skipped(tmp_path):
    path = _make_zip({"companyfacts_index.json": b'{"meta": true}'}, tmp_path / "cf.zip")
    records = list(_READER.iter_target_records(path, _TARGET))
    assert records == []


# ── Size limits ──────────────────────────────────────────────────────────────


def test_oversized_member_skipped(tmp_path):
    tiny_reader = CompanyFactsArchiveReader(max_member_bytes=10)
    path = _make_zip({"CIK0000723125.json": _cf_json()}, tmp_path / "cf.zip")
    stats = CompanyFactsArchiveStats()
    records = list(tiny_reader.iter_target_records(path, _TARGET, stats=stats))
    assert records == []
    assert stats.skipped_other >= 1


def test_member_at_exactly_max_bytes_accepted(tmp_path):
    data = _cf_json()
    exact_reader = CompanyFactsArchiveReader(max_member_bytes=len(data))
    path = _make_zip({"CIK0000723125.json": data}, tmp_path / "cf.zip")
    records = list(exact_reader.iter_target_records(path, _TARGET))
    assert len(records) == 1


# ── CIK mismatch ────────────────────────────────────────────────────────────


def test_cik_mismatch_yields_record_with_flag(tmp_path):
    # Filename says 0000723125 but payload says a different CIK.
    payload = json.dumps({
        "cik": "CIK0000000001",
        "name": "Wrong Corp",
        "facts": {},
    }).encode()
    path = _make_zip({"CIK0000723125.json": payload}, tmp_path / "cf.zip")
    stats = CompanyFactsArchiveStats()
    records = list(_READER.iter_target_records(path, _TARGET, stats=stats))
    assert len(records) == 1
    assert records[0].cik_mismatch is True
    assert stats.cik_mismatches == 1


def test_matching_cik_has_no_mismatch_flag(tmp_path):
    path = _make_zip({"CIK0000723125.json": _cf_json()}, tmp_path / "cf.zip")
    records = list(_READER.iter_target_records(path, _TARGET))
    assert records[0].cik_mismatch is False


# ── Stats tracking ───────────────────────────────────────────────────────────


def test_stats_found_ciks_populated_after_iteration(tmp_path):
    path = _make_zip({"CIK0000723125.json": _cf_json()}, tmp_path / "cf.zip")
    stats = CompanyFactsArchiveStats()
    list(_READER.iter_target_records(path, _TARGET, stats=stats))
    assert "0000723125" in stats.found_ciks
    assert stats.targeted_found == 1


def test_missing_cik_not_in_found_ciks(tmp_path):
    path = _make_zip({}, tmp_path / "cf.zip")
    stats = CompanyFactsArchiveStats()
    list(_READER.iter_target_records(path, _TARGET, stats=stats))
    assert stats.found_ciks == set()


# ── Directory-prefix member accepted ────────────────────────────────────────


def test_directory_prefix_member_accepted(tmp_path):
    path = _make_zip({"subdir/CIK0000723125.json": _cf_json()}, tmp_path / "cf.zip")
    records = list(_READER.iter_target_records(path, _TARGET))
    assert len(records) == 1
    assert records[0].cik == "0000723125"
