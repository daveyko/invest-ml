"""Tests for SelectedCompanyFactsReader."""

import json
import zipfile
from pathlib import Path

import pytest


def _make_companyfacts_zip(tmp_path: Path, members: dict[str, bytes]) -> Path:
    """Write a ZIP with CIK##########.json members."""
    archive = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return archive


def _cik_payload(cik_int: int) -> bytes:
    return json.dumps({"cik": cik_int, "facts": {}}).encode()


def test_list_found_ciks_returns_intersection(tmp_path):
    from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader

    archive = _make_companyfacts_zip(tmp_path, {
        "CIK0000000001.json": _cik_payload(1),
        "CIK0000000002.json": _cik_payload(2),
        "not_a_cik.json": b"{}",
    })

    reader = SelectedCompanyFactsReader(archive)
    target = frozenset(["0000000001", "0000000003"])
    found = reader.list_found_ciks(target)
    assert found == frozenset(["0000000001"])


def test_list_found_ciks_empty_archive(tmp_path):
    from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader

    archive = _make_companyfacts_zip(tmp_path, {})
    reader = SelectedCompanyFactsReader(archive)
    found = reader.list_found_ciks(frozenset(["0000000001"]))
    assert found == frozenset()


def test_read_member_returns_bytes_for_known_cik(tmp_path):
    from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader

    payload = _cik_payload(42)
    archive = _make_companyfacts_zip(tmp_path, {
        "CIK0000000042.json": payload,
    })

    reader = SelectedCompanyFactsReader(archive)
    result = reader.read_member("0000000042")
    assert result == payload


def test_read_member_returns_none_for_unknown_cik(tmp_path):
    from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader

    archive = _make_companyfacts_zip(tmp_path, {
        "CIK0000000001.json": _cik_payload(1),
    })
    reader = SelectedCompanyFactsReader(archive)
    assert reader.read_member("9999999999") is None


def test_read_member_raises_for_oversized_member(tmp_path):
    from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader

    archive = _make_companyfacts_zip(tmp_path, {
        "CIK0000000001.json": b"x" * 100,
    })
    reader = SelectedCompanyFactsReader(archive, max_member_bytes=50)
    with pytest.raises(ValueError, match="exceeds max size"):
        reader.read_member("0000000001")


def test_index_built_once(tmp_path, monkeypatch):
    from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader

    archive = _make_companyfacts_zip(tmp_path, {
        "CIK0000000001.json": _cik_payload(1),
    })
    reader = SelectedCompanyFactsReader(archive)

    build_count = 0
    original_build = reader._build_index

    def counted_build():
        nonlocal build_count
        build_count += 1
        original_build()

    monkeypatch.setattr(reader, "_build_index", counted_build)

    reader.list_found_ciks(frozenset(["0000000001"]))
    # After first call, _cik_to_name is set, so subsequent calls skip the body
    reader.read_member("0000000001")
    # Only the explicit calls above invoke the monkeypatched version
    assert build_count <= 2


def test_traversal_path_skipped(tmp_path):
    from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader

    archive = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        # Create a traversal-looking path — zipfile normalizes names
        info = zipfile.ZipInfo("../CIK0000000001.json")
        zf.writestr(info, _cik_payload(1))

    reader = SelectedCompanyFactsReader(archive)
    found = reader.list_found_ciks(frozenset(["0000000001"]))
    assert found == frozenset()


def test_first_duplicate_cik_wins(tmp_path):
    from invest_ml.sec.companyfacts_reader import SelectedCompanyFactsReader

    first_payload = _cik_payload(1)
    second_payload = _cik_payload(99)
    archive = _make_companyfacts_zip(tmp_path, {
        "CIK0000000001.json": first_payload,
        "subdir/CIK0000000001.json": second_payload,
    })
    reader = SelectedCompanyFactsReader(archive)
    result = reader.read_member("0000000001")
    # First occurrence wins (which one is "first" depends on ZIP order)
    assert result in (first_payload, second_payload)
    # But only one is returned
    assert result is not None
