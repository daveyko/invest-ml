"""Unit tests for SubmissionArchiveReader.

All tests use in-memory ZIP fixtures written to pytest's tmp_path.
No network activity.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from invest_ml.sec.archive import SubmissionArchiveReader

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_zip(dest: Path, members: dict[str, bytes]) -> Path:
    """Write a ZIP to dest containing the given filename → bytes members."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    dest.write_bytes(buf.getvalue())
    return dest


def _company_json(cik: str = "0000000001", name: str = "Test Corp") -> bytes:
    return json.dumps({"cik": cik, "name": name, "tickers": [], "exchanges": []}).encode()


def _reader(max_bytes: int = 50 * 1024 * 1024) -> SubmissionArchiveReader:
    return SubmissionArchiveReader(max_member_bytes=max_bytes)


# ── Primary record yielded ────────────────────────────────────────────────────


def test_primary_record_is_yielded(tmp_path):
    payload = _company_json("0000723125", "MICRON TECHNOLOGY INC")
    _write_zip(tmp_path / "test.zip", {"CIK0000723125.json": payload})

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert len(records) == 1
    assert records[0].member_name == "CIK0000723125.json"
    assert records[0].payload == payload


def test_multiple_primary_records_all_yielded(tmp_path):
    members = {
        "CIK0000000001.json": _company_json("0000000001", "Alpha Inc"),
        "CIK0000000002.json": _company_json("0000000002", "Beta Corp"),
        "CIK0000000003.json": _company_json("0000000003", "Gamma LLC"),
    }
    _write_zip(tmp_path / "test.zip", members)

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert len(records) == 3
    member_names = {r.member_name for r in records}
    assert member_names == set(members.keys())


# ── Shard files skipped ───────────────────────────────────────────────────────


def test_shard_file_is_skipped(tmp_path):
    members = {
        "CIK0000000001.json": _company_json(),
        "CIK0000000001-submissions-001.json": b'{"cik":"0000000001","name":"overflow shard"}',
    }
    _write_zip(tmp_path / "test.zip", members)

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert len(records) == 1
    assert records[0].member_name == "CIK0000000001.json"


# ── Non-JSON members skipped ─────────────────────────────────────────────────


def test_non_json_file_is_skipped(tmp_path):
    members = {
        "CIK0000000001.json": _company_json(),
        "README.txt": b"ignore me",
    }
    _write_zip(tmp_path / "test.zip", members)

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert len(records) == 1


# ── Path traversal guard ─────────────────────────────────────────────────────


def test_traversal_sequence_is_rejected(tmp_path):
    members = {
        "../CIK0000000001.json": _company_json(),
    }
    _write_zip(tmp_path / "test.zip", members)

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert records == []


# ── Size cap ─────────────────────────────────────────────────────────────────


def test_oversized_member_is_skipped(tmp_path):
    large_payload = _company_json() + b" " * 500
    _write_zip(tmp_path / "test.zip", {"CIK0000000001.json": large_payload})

    records = list(_reader(max_bytes=50).iter_company_records(tmp_path / "test.zip"))

    assert records == []


def test_member_at_exactly_max_bytes_is_accepted(tmp_path):
    payload = _company_json()
    _write_zip(tmp_path / "test.zip", {"CIK0000000001.json": payload})

    records = list(_reader(max_bytes=len(payload)).iter_company_records(tmp_path / "test.zip"))

    assert len(records) == 1


# ── Missing required fields ───────────────────────────────────────────────────


def test_member_without_cik_field_is_skipped(tmp_path):
    payload = json.dumps({"name": "No CIK Corp"}).encode()
    _write_zip(tmp_path / "test.zip", {"CIK0000000001.json": payload})

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert records == []


def test_member_without_name_field_is_skipped(tmp_path):
    payload = json.dumps({"cik": "0000000001"}).encode()
    _write_zip(tmp_path / "test.zip", {"CIK0000000001.json": payload})

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert records == []


# ── Malformed JSON ────────────────────────────────────────────────────────────


def test_malformed_json_member_does_not_abort_iteration(tmp_path):
    members = {
        "CIK0000000001.json": b"{ not valid json !!!",
        "CIK0000000002.json": _company_json("0000000002", "Good Corp"),
    }
    _write_zip(tmp_path / "test.zip", members)

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    # CIK1 fails (no valid "cik"/"name" from JSON parse), CIK2 succeeds.
    assert len(records) == 1
    assert records[0].member_name == "CIK0000000002.json"


# ── Non-primary filename patterns ─────────────────────────────────────────────


def test_non_cik_json_filename_is_skipped(tmp_path):
    members = {
        "company-info.json": _company_json(),
        "CIK0000000001.json": _company_json("0000000001", "Real"),
    }
    _write_zip(tmp_path / "test.zip", members)

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert len(records) == 1
    assert records[0].member_name == "CIK0000000001.json"


# ── Subdirectory prefix allowed ───────────────────────────────────────────────


def test_primary_record_in_subdirectory_is_accepted(tmp_path):
    payload = _company_json("0000000001", "Subdir Corp")
    _write_zip(tmp_path / "test.zip", {"submissions/CIK0000000001.json": payload})

    records = list(_reader().iter_company_records(tmp_path / "test.zip"))

    assert len(records) == 1
    assert records[0].payload == payload
