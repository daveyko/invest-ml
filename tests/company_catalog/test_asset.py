"""Unit tests for the company_catalog Dagster asset.

The asset function is called directly with mocked resources.
No database, no network, no real Dagster execution context required.

Patch targets use the *source* module paths because the asset imports
CompanyCatalogRepository / CompanyCatalogService / SubmissionArchiveReader
lazily inside the function body via `from X import Y`.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from dagster import MaterializeResult, build_asset_context

from invest_ml.company_catalog.models import CompanyCatalogResult
from invest_ml.defs.assets.discovery import company_catalog
from invest_ml.sec.client import DownloadResult, SecDownloadError

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

_REPO_PATH = "invest_ml.db.repositories.company_catalog.CompanyCatalogRepository"
_SVC_PATH = "invest_ml.company_catalog.service.CompanyCatalogService"
_READER_PATH = "invest_ml.sec.archive.SubmissionArchiveReader"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _minimal_zip(tmp_path: Path) -> Path:
    p = tmp_path / "submissions.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dummy.txt", b"ok")
    p.write_bytes(buf.getvalue())
    return p


def _make_download_result(
    archive_path: Path,
    *,
    sha256: str = "deadbeef" * 8,
    not_modified: bool = False,
) -> DownloadResult:
    return DownloadResult(
        path=archive_path if not not_modified else Path(""),
        sha256=sha256 if not not_modified else "",
        byte_size=archive_path.stat().st_size if (not not_modified and archive_path.exists()) else 0,
        downloaded_at=_NOW,
        etag='"v1"',
        last_modified="Thu, 15 Jan 2026 12:00:00 GMT",
        not_modified=not_modified,
    )


def _make_session_factory():
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=session)


def _make_postgres(session_factory) -> MagicMock:
    pg = MagicMock()
    pg.get_session_factory.return_value = session_factory
    return pg


def _make_sec_bulk(tmp_path: Path, *, retain: bool = False) -> MagicMock:
    sb = MagicMock()
    sb.submissions_bulk_url = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
    sb.download_dir_path = tmp_path
    sb.max_zip_member_bytes = 50 * 1024 * 1024
    sb.retain_archives = retain
    sb.find_cached_archive.return_value = None  # no local cache hit by default
    return sb


def _make_context():
    return build_asset_context()


def _make_run(run_id=None):
    run = MagicMock()
    run.run_id = run_id or uuid4()
    return run


def _catalog_result(**kw) -> CompanyCatalogResult:
    defaults = dict(
        companies_seen=100,
        companies_inserted=80,
        companies_updated=20,
        securities_inserted=90,
        securities_updated=10,
        sic_classifications_inserted=70,
        parse_warnings=[],
        malformed_records=2,
    )
    defaults.update(kw)
    return CompanyCatalogResult(**defaults)


# ── Test: full first-run path ─────────────────────────────────────────────────


def test_full_run_materializes_with_correct_metadata(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path)
    context = _make_context()

    mock_client = MagicMock()
    mock_client.download_submissions_archive.return_value = _make_download_result(archive)
    sec_bulk.make_client.return_value = mock_client

    mock_run = _make_run(run_id)
    expected_catalog = _catalog_result()

    with (
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_SVC_PATH) as mock_svc_cls,
        patch(_READER_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = mock_run
        mock_repo.find_latest_successful_ingestion_run.return_value = None

        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.refresh_catalog.return_value = expected_catalog

        result = company_catalog(context, postgres, sec_bulk, MagicMock())

    assert isinstance(result, MaterializeResult)
    assert result.metadata["changed"].value is True
    assert result.metadata["companies_seen"].value == 100
    assert result.metadata["companies_inserted"].value == 80
    assert result.metadata["malformed_records"].value == 2


def test_full_run_calls_succeed_ingestion_run(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_submissions_archive.return_value = _make_download_result(
        archive, sha256="abc123"
    )
    sec_bulk.make_client.return_value = mock_client

    mock_run = _make_run(run_id)

    with (
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_SVC_PATH) as mock_svc_cls,
        patch(_READER_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = mock_run
        mock_repo.find_latest_successful_ingestion_run.return_value = None

        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.refresh_catalog.return_value = _catalog_result()

        company_catalog(_make_context(), postgres, sec_bulk, MagicMock())

    mock_repo.succeed_ingestion_run.assert_called_once()
    positional = mock_repo.succeed_ingestion_run.call_args[0]
    assert positional[0] == run_id


def test_archive_deleted_after_processing_when_retain_false(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path, retain=False)

    mock_client = MagicMock()
    mock_client.download_submissions_archive.return_value = _make_download_result(archive)
    sec_bulk.make_client.return_value = mock_client

    with (
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_SVC_PATH) as mock_svc_cls,
        patch(_READER_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = _make_run(run_id)
        mock_repo.find_latest_successful_ingestion_run.return_value = None

        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.refresh_catalog.return_value = _catalog_result()

        company_catalog(_make_context(), postgres, sec_bulk, MagicMock())

    assert not archive.exists()


def test_archive_kept_when_retain_true(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path, retain=True)

    mock_client = MagicMock()
    mock_client.download_submissions_archive.return_value = _make_download_result(archive)
    sec_bulk.make_client.return_value = mock_client

    with (
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_SVC_PATH) as mock_svc_cls,
        patch(_READER_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = _make_run(run_id)
        mock_repo.find_latest_successful_ingestion_run.return_value = None

        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.refresh_catalog.return_value = _catalog_result()

        company_catalog(_make_context(), postgres, sec_bulk, MagicMock())

    assert archive.exists()


# ── Test: local cache hit ─────────────────────────────────────────────────────


def test_local_cache_hit_skips_download_and_returns_changed_false(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()
    sha = "b" * 64

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path)
    sec_bulk.find_cached_archive.return_value = archive  # cache hit

    prev_run = MagicMock()
    prev_run.archive_hash = sha
    prev_run.etag = '"v0"'
    prev_run.last_modified = "Mon, 01 Jan 2024 00:00:00 GMT"

    with (
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_SVC_PATH) as mock_svc_cls,
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = _make_run(run_id)
        mock_repo.find_latest_successful_ingestion_run.return_value = prev_run

        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc

        result = company_catalog(_make_context(), postgres, sec_bulk, MagicMock())

    assert result.metadata["changed"].value is False
    assert result.metadata["skip_reason"].value == "local cache hit"
    # No HTTP download occurred.
    sec_bulk.make_client.assert_not_called()
    mock_svc.refresh_catalog.assert_not_called()


# ── Test: HTTP 304 / unchanged archive ───────────────────────────────────────


def test_304_skips_parsing_and_returns_changed_false(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_submissions_archive.return_value = _make_download_result(
        archive, not_modified=True
    )
    sec_bulk.make_client.return_value = mock_client

    with (
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_SVC_PATH) as mock_svc_cls,
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = _make_run(run_id)
        mock_repo.find_latest_successful_ingestion_run.return_value = None

        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc

        result = company_catalog(_make_context(), postgres, sec_bulk, MagicMock())

    assert isinstance(result, MaterializeResult)
    assert result.metadata["changed"].value is False
    mock_svc.refresh_catalog.assert_not_called()


def test_same_sha256_skips_parsing(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()
    sha = "a" * 64

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_submissions_archive.return_value = _make_download_result(
        archive, sha256=sha
    )
    sec_bulk.make_client.return_value = mock_client

    prev_run = MagicMock()
    prev_run.archive_hash = sha
    prev_run.etag = '"v0"'
    prev_run.last_modified = "Mon, 01 Jan 2024 00:00:00 GMT"

    with (
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_SVC_PATH) as mock_svc_cls,
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = _make_run(run_id)
        mock_repo.find_latest_successful_ingestion_run.return_value = prev_run

        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc

        result = company_catalog(_make_context(), postgres, sec_bulk, MagicMock())

    assert result.metadata["changed"].value is False
    mock_svc.refresh_catalog.assert_not_called()


# ── Test: failure path ────────────────────────────────────────────────────────


def test_download_error_marks_run_failed_and_reraises(tmp_path):
    run_id = uuid4()

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_submissions_archive.side_effect = SecDownloadError("timeout")
    sec_bulk.make_client.return_value = mock_client

    with patch(_REPO_PATH) as mock_repo_cls:
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = _make_run(run_id)
        mock_repo.find_latest_successful_ingestion_run.return_value = None

        with pytest.raises(SecDownloadError):
            company_catalog(_make_context(), postgres, sec_bulk, MagicMock())

    mock_repo.fail_ingestion_run.assert_called_once()
    fail_args = mock_repo.fail_ingestion_run.call_args
    assert fail_args[0][0] == run_id
    assert "timeout" in fail_args[1]["error"]


def test_service_exception_marks_run_failed_and_reraises(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()

    session_factory = _make_session_factory()
    postgres = _make_postgres(session_factory)
    sec_bulk = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_submissions_archive.return_value = _make_download_result(archive)
    sec_bulk.make_client.return_value = mock_client

    with (
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_SVC_PATH) as mock_svc_cls,
        patch(_READER_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.create_ingestion_run.return_value = _make_run(run_id)
        mock_repo.find_latest_successful_ingestion_run.return_value = None

        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.refresh_catalog.side_effect = RuntimeError("db explosion")

        with pytest.raises(RuntimeError, match="db explosion"):
            company_catalog(_make_context(), postgres, sec_bulk, MagicMock())

    mock_repo.fail_ingestion_run.assert_called_once()
