"""Tests for SecBulkArchiveCache."""

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest


def _make_valid_zip(path: Path, content: bytes = b"test") -> str:
    """Write a minimal valid ZIP to path and return its SHA-256."""
    import hashlib

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("dummy.json", content)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return sha256


def test_get_or_refresh_uses_cache_when_fresh(tmp_path):
    from invest_ml.sec.archive_cache import SecBulkArchiveCache

    archives_dir = tmp_path / "archives"
    archives_dir.mkdir()
    manifest_path = tmp_path / "current.json"

    archive_path = archives_dir / "abc123.zip"
    sha256 = _make_valid_zip(archive_path)
    archive_path.rename(archives_dir / f"{sha256}.zip")
    actual_path = archives_dir / f"{sha256}.zip"

    manifest = {
        "sha256": sha256,
        "byte_size": actual_path.stat().st_size,
        "downloaded_at": datetime.now(tz=UTC).isoformat(),
        "etag": None,
        "last_modified": None,
    }
    manifest_path.write_text(json.dumps(manifest))

    cache = SecBulkArchiveCache(
        cache_dir=tmp_path,
        companyfacts_bulk_url="https://example.com/companyfacts.zip",
        user_agent="test-agent test@test.com",
        remote_check_after_hours=24.0,
    )

    result = cache.get_or_refresh(force_refresh=False, cache_only=True)
    assert result.path == actual_path
    assert result.sha256 == sha256
    assert not result.was_refreshed


def test_get_or_refresh_raises_cache_only_with_no_manifest(tmp_path):
    from invest_ml.sec.archive_cache import SecBulkArchiveCache

    cache = SecBulkArchiveCache(
        cache_dir=tmp_path,
        companyfacts_bulk_url="https://example.com/companyfacts.zip",
        user_agent="test-agent test@test.com",
    )
    with pytest.raises(RuntimeError, match="cache_only=True"):
        cache.get_or_refresh(cache_only=True)


def test_get_or_refresh_raises_cache_only_with_missing_archive(tmp_path):
    from invest_ml.sec.archive_cache import SecBulkArchiveCache

    manifest = {
        "sha256": "nonexistent" * 4,
        "byte_size": 0,
        "downloaded_at": datetime.now(tz=UTC).isoformat(),
        "etag": None,
        "last_modified": None,
    }
    (tmp_path / "current.json").write_text(json.dumps(manifest))

    cache = SecBulkArchiveCache(
        cache_dir=tmp_path,
        companyfacts_bulk_url="https://example.com/companyfacts.zip",
        user_agent="test-agent test@test.com",
    )
    with pytest.raises(RuntimeError, match="cache_only=True"):
        cache.get_or_refresh(cache_only=True)


def test_validate_cached_returns_none_for_nonexistent_archive(tmp_path):
    from invest_ml.sec.archive_cache import SecBulkArchiveCache

    cache = SecBulkArchiveCache(
        cache_dir=tmp_path,
        companyfacts_bulk_url="https://example.com/companyfacts.zip",
        user_agent="test-agent test@test.com",
    )
    manifest = {"sha256": "abc" * 20, "byte_size": 0, "downloaded_at": datetime.now(tz=UTC).isoformat()}
    result = cache._validate_cached(manifest)
    assert result is None


def test_validate_cached_returns_none_for_invalid_zip(tmp_path):
    from invest_ml.sec.archive_cache import SecBulkArchiveCache

    archives_dir = tmp_path / "archives"
    archives_dir.mkdir()
    sha256 = "a" * 64
    bad_zip = archives_dir / f"{sha256}.zip"
    bad_zip.write_bytes(b"not a zip")

    cache = SecBulkArchiveCache(
        cache_dir=tmp_path,
        companyfacts_bulk_url="https://example.com/companyfacts.zip",
        user_agent="test-agent test@test.com",
    )
    manifest = {"sha256": sha256, "byte_size": 9, "downloaded_at": datetime.now(tz=UTC).isoformat()}
    result = cache._validate_cached(manifest)
    assert result is None


def test_manifest_written_atomically(tmp_path):
    from invest_ml.sec.archive_cache import SecBulkArchiveCache

    archives_dir = tmp_path / "archives"
    archives_dir.mkdir()

    cache = SecBulkArchiveCache(
        cache_dir=tmp_path,
        companyfacts_bulk_url="https://example.com/companyfacts.zip",
        user_agent="test-agent test@test.com",
    )
    cache._write_manifest({"sha256": "test", "downloaded_at": datetime.now(tz=UTC).isoformat()})
    assert (tmp_path / "current.json").exists()
    assert not (tmp_path / "current.json.tmp").exists()
