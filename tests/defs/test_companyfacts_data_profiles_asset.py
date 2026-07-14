"""Unit tests for the companyfacts_data_profiles Dagster asset.

All external dependencies are mocked — no database, network, or real archive.
Patch paths use the *source* module paths because the asset imports lazily
inside the function body.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from dagster import MaterializeResult, build_asset_context

from invest_ml.db.repositories.company_data_profiles import (
    CompanyProfileTarget,
    ProfileUpsertResult,
)
from invest_ml.defs.assets.discovery import companyfacts_data_profiles
from invest_ml.sec.client import DownloadResult, SecDownloadError

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

# Source-module patch paths (lazy imports inside asset body)
_REPO_PATH = "invest_ml.db.repositories.company_data_profiles.CompanyDataProfileRepository"
_READER_PATH = "invest_ml.sec.companyfacts_archive.CompanyFactsArchiveReader"
_PROFILER_PATH = "invest_ml.sec.profiler.CompanyFactsProfiler"
_PROFILING_CONFIG_PATH = "invest_ml.sec.profiler.ProfilingConfig"
_LOAD_METRICS = "invest_ml.config.loaders.load_canonical_metrics"
_LOAD_UNIVERSE = "invest_ml.config.loaders.load_universe_config"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _minimal_zip(tmp_path: Path) -> Path:
    p = tmp_path / "companyfacts.zip"
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
    sb.companyfacts_bulk_url = (
        "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
    )
    sb.download_dir_path = tmp_path
    sb.max_zip_member_bytes = 50 * 1024 * 1024
    sb.retain_archives = retain
    sb.companyfacts_profile_version = "companyfacts_profile_v1"
    sb.find_cached_archive.return_value = None
    return sb


def _make_target(cik: str = "0000723125") -> CompanyProfileTarget:
    return CompanyProfileTarget(company_id=uuid4(), cik=cik)


def _minimal_metrics_cfg() -> dict:
    return {
        "profiling": {
            "required_metrics": ["revenue"],
            "annual_forms": ["10-K"],
            "quarterly_forms": ["10-Q"],
            "metrics": {
                "revenue": {
                    "period_kind": "duration",
                    "units": ["USD"],
                    "concepts": [{"taxonomy": "us-gaap", "tag": "Revenues"}],
                }
            },
        },
        "metrics": {},
    }


def _minimal_universe_cfg() -> dict:
    return {
        "candidate": {"exchanges": ["Nasdaq", "NYSE"]},
        "training": {"minimum_annual_periods": 4, "minimum_price_history_years": 3,
                     "minimum_canonical_metric_coverage": 0.5},
        "scoring": {"model_buckets": [], "always_include": []},
    }


def _make_context():
    return build_asset_context()


# ── Full-run tests ────────────────────────────────────────────────────────────


def test_full_run_materializes_with_changed_true(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()
    target = _make_target()

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(archive)
    sb.make_client.return_value = mock_client

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_READER_PATH) as mock_reader_cls,
        patch(_PROFILER_PATH) as mock_profiler_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = run_id
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = [target]
        mock_repo.upsert_profiles.return_value = ProfileUpsertResult(upserted=1)

        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader
        mock_reader.iter_target_records.return_value = iter([])  # all missing

        mock_profiler = MagicMock()
        mock_profiler_cls.return_value = mock_profiler
        mock_profiler.profile_missing.return_value = MagicMock()

        result = companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    assert isinstance(result, MaterializeResult)
    assert result.metadata["changed"].value is True
    assert result.metadata["targets"].value == 1


def test_full_run_calls_succeed_ingestion_run(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(archive, sha256="abc" * 21 + "d")
    sb.make_client.return_value = mock_client

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_READER_PATH) as mock_reader_cls,
        patch(_PROFILER_PATH) as mock_profiler_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = run_id
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = []
        mock_repo.upsert_profiles.return_value = ProfileUpsertResult(upserted=0)

        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader
        mock_reader.iter_target_records.return_value = iter([])

        mock_profiler = MagicMock()
        mock_profiler_cls.return_value = mock_profiler

        companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    mock_repo.succeed_ingestion_run.assert_called_once()
    call_args = mock_repo.succeed_ingestion_run.call_args
    assert call_args[0][0] == run_id


# ── Archive retention ─────────────────────────────────────────────────────────


def test_archive_deleted_when_retain_false(tmp_path):
    archive = _minimal_zip(tmp_path)

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path, retain=False)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(archive)
    sb.make_client.return_value = mock_client

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_READER_PATH) as mock_reader_cls,
        patch(_PROFILER_PATH) as mock_profiler_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = uuid4()
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = []
        mock_repo.upsert_profiles.return_value = ProfileUpsertResult(upserted=0)
        mock_reader_cls.return_value.iter_target_records.return_value = iter([])
        mock_profiler_cls.return_value = MagicMock()

        companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    assert not archive.exists()


def test_archive_kept_when_retain_true(tmp_path):
    archive = _minimal_zip(tmp_path)

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path, retain=True)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(archive)
    sb.make_client.return_value = mock_client

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_READER_PATH) as mock_reader_cls,
        patch(_PROFILER_PATH) as mock_profiler_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = uuid4()
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = []
        mock_repo.upsert_profiles.return_value = ProfileUpsertResult(upserted=0)
        mock_reader_cls.return_value.iter_target_records.return_value = iter([])
        mock_profiler_cls.return_value = MagicMock()

        companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    assert archive.exists()


# ── Cache / skip paths ────────────────────────────────────────────────────────


def test_local_cache_hit_skips_download_returns_changed_false(tmp_path):
    archive = _minimal_zip(tmp_path)
    sha = "c" * 64

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)
    sb.find_cached_archive.return_value = archive  # cache hit

    prev_run = MagicMock()
    prev_run.archive_hash = sha
    prev_run.etag = '"v0"'
    prev_run.last_modified = "Mon, 01 Jan 2024 00:00:00 GMT"

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = uuid4()
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = prev_run
        mock_repo.list_companyfacts_profile_targets.return_value = []

        result = companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    assert result.metadata["changed"].value is False
    assert result.metadata["skip_reason"].value == "local cache hit"
    sb.make_client.assert_not_called()


def test_304_skips_profiling_returns_changed_false(tmp_path):
    archive = _minimal_zip(tmp_path)

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(
        archive, not_modified=True
    )
    sb.make_client.return_value = mock_client

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = uuid4()
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = []

        result = companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    assert result.metadata["changed"].value is False
    assert "304" in result.metadata["skip_reason"].value


def test_same_sha256_skips_profiling(tmp_path):
    archive = _minimal_zip(tmp_path)
    sha = "a" * 64

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(archive, sha256=sha)
    sb.make_client.return_value = mock_client

    prev_run = MagicMock()
    prev_run.archive_hash = sha
    prev_run.etag = '"v0"'
    prev_run.last_modified = "Mon, 01 Jan 2024 00:00:00 GMT"

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = uuid4()
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = prev_run
        mock_repo.list_companyfacts_profile_targets.return_value = []

        result = companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    assert result.metadata["changed"].value is False


# ── Failure paths ─────────────────────────────────────────────────────────────


def test_download_error_marks_run_failed_and_reraises(tmp_path):
    run_id = uuid4()

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_archive.side_effect = SecDownloadError("connection reset")
    sb.make_client.return_value = mock_client

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = run_id
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = []

        with pytest.raises(SecDownloadError):
            companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    mock_repo.fail_ingestion_run.assert_called_once()
    assert mock_repo.fail_ingestion_run.call_args[0][0] == run_id
    assert "connection reset" in mock_repo.fail_ingestion_run.call_args[1]["error"]


def test_upsert_exception_marks_run_failed(tmp_path):
    archive = _minimal_zip(tmp_path)
    run_id = uuid4()
    # Need at least one target so a missing profile is created and upsert_profiles is called.
    target = _make_target("0000723125")

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(archive)
    sb.make_client.return_value = mock_client

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_READER_PATH) as mock_reader_cls,
        patch(_PROFILER_PATH) as mock_profiler_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = run_id
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = [target]
        mock_repo.upsert_profiles.side_effect = RuntimeError("db error")

        mock_reader_cls.return_value.iter_target_records.return_value = iter([])
        mock_profiler = MagicMock()
        mock_profiler_cls.return_value = mock_profiler
        mock_profiler.profile_missing.return_value = MagicMock()

        with pytest.raises(RuntimeError, match="db error"):
            companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    mock_repo.fail_ingestion_run.assert_called_once()


# ── Missing-CIK profiles ──────────────────────────────────────────────────────


def test_missing_ciks_get_empty_profiles(tmp_path):
    archive = _minimal_zip(tmp_path)
    target = _make_target("0000723125")

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(archive)
    sb.make_client.return_value = mock_client

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_READER_PATH) as mock_reader_cls,
        patch(_PROFILER_PATH) as mock_profiler_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = uuid4()
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = [target]
        mock_repo.upsert_profiles.return_value = ProfileUpsertResult(upserted=1)

        # Reader yields nothing → CIK is "missing"
        mock_reader_cls.return_value.iter_target_records.return_value = iter([])

        mock_profiler = MagicMock()
        mock_profiler_cls.return_value = mock_profiler
        mock_profiler.profile_missing.return_value = MagicMock()

        companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    # profile_missing called once for the missing CIK
    mock_profiler.profile_missing.assert_called_once()
    call_kwargs = mock_profiler.profile_missing.call_args[1]
    assert call_kwargs["cik"] == "0000723125"


def test_metadata_reflects_found_and_missing_counts(tmp_path):
    archive = _minimal_zip(tmp_path)
    target = _make_target("0000723125")

    sf = _make_session_factory()
    pg = _make_postgres(sf)
    sb = _make_sec_bulk(tmp_path)

    mock_client = MagicMock()
    mock_client.download_archive.return_value = _make_download_result(archive)
    sb.make_client.return_value = mock_client

    def _reader_side_effect(archive_path, target_ciks, stats):
        stats.found_ciks.add("0000723125")
        stats.targeted_found = 1
        return iter([])

    with (
        patch(_LOAD_METRICS, return_value=_minimal_metrics_cfg()),
        patch(_LOAD_UNIVERSE, return_value=_minimal_universe_cfg()),
        patch(_REPO_PATH) as mock_repo_cls,
        patch(_READER_PATH) as mock_reader_cls,
        patch(_PROFILER_PATH) as mock_profiler_cls,
        patch(_PROFILING_CONFIG_PATH),
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        run = MagicMock(); run.run_id = uuid4()
        mock_repo.create_ingestion_run.return_value = run
        mock_repo.find_latest_successful_ingestion_run.return_value = None
        mock_repo.list_companyfacts_profile_targets.return_value = [target]
        mock_repo.upsert_profiles.return_value = ProfileUpsertResult(upserted=1)

        mock_reader = MagicMock()
        mock_reader_cls.return_value = mock_reader
        mock_reader.iter_target_records.side_effect = _reader_side_effect

        mock_profiler_cls.return_value = MagicMock()

        result = companyfacts_data_profiles(_make_context(), pg, sb, MagicMock())

    # CIK was found so missing should be 0
    assert result.metadata["found_in_archive"].value == 1
    assert result.metadata["missing_in_archive"].value == 0
