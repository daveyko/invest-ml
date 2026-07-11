"""Tests for the candidate_universe Dagster asset.

Patches CandidateUniverseService and UniverseRepository so no DB is required.
Uses build_asset_context() — MagicMock fails Dagster's isinstance check.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from dagster import build_asset_context

from invest_ml.defs.assets.discovery import candidate_universe
from invest_ml.universe.models import CandidateUniverseResult

_SERVICE_PATH = "invest_ml.universe.service.CandidateUniverseService"
_REPO_PATH = "invest_ml.db.repositories.universe.UniverseRepository"
_LOAD_UNIVERSE = "invest_ml.config.loaders.load_universe_config"


def _make_postgres_resource() -> MagicMock:
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    resource = MagicMock()
    resource.get_session_factory.return_value = MagicMock(return_value=mock_session)
    return resource


def _universe_config() -> dict:
    return {
        "candidate": {
            "name": "candidate",
            "version": "v1",
            "supported_exchanges": ["Nasdaq"],
            "exchange_aliases": {"Nasdaq": "Nasdaq"},
            "allowed_entity_types": ["operating"],
            "excluded_exact_entity_types": [],
            "exclude_missing_entity_type": True,
            "require_current_ticker": True,
            "require_company_data_profile": False,
            "recent_filing_months": 18,
            "exclude_missing_recent_filing": True,
            "excluded_sic_codes": [],
            "excluded_name_patterns": [],
            "always_exclude_ciks": [],
            "always_include_ciks": [],
            "profile_version": "companyfacts_profile_v1",
        }
    }


def _mock_result() -> CandidateUniverseResult:
    return CandidateUniverseResult(
        evaluated_companies=100,
        included_companies=60,
        newly_included=55,
        already_included=5,
        newly_excluded=3,
        exclusion_counts={"stale_latest_filing": 20, "missing_entity_type": 15},
        universe_id=uuid4(),
        criteria_hash="abc123" * 10,
    )


def test_candidate_universe_asset_happy_path():
    """Asset calls service.materialize and returns MaterializeResult with correct metadata."""
    pg = _make_postgres_resource()
    run_mock = MagicMock()
    run_mock.run_id = uuid4()

    universe_result = _mock_result()

    with (
        patch(_LOAD_UNIVERSE, return_value=_universe_config()),
        patch(_REPO_PATH) as MockRepo,
        patch(_SERVICE_PATH) as MockService,
    ):
        repo_inst = MockRepo.return_value
        repo_inst.create_ingestion_run.return_value = run_mock
        service_inst = MockService.return_value
        service_inst.materialize.return_value = universe_result

        ctx = build_asset_context()
        result = candidate_universe(ctx, postgres=pg)

    assert result is not None
    meta = result.metadata
    assert int(meta["evaluated_companies"].value) == 100
    assert int(meta["included_companies"].value) == 60
    assert int(meta["newly_included"].value) == 55
    assert int(meta["newly_excluded"].value) == 3

    service_inst.materialize.assert_called_once()
    repo_inst.succeed_ingestion_run.assert_called_once()


def test_candidate_universe_asset_marks_run_failed_on_exception():
    """When service.materialize raises, the IngestionRun is marked failed before re-raise."""
    pg = _make_postgres_resource()
    run_mock = MagicMock()
    run_mock.run_id = uuid4()

    with (
        patch(_LOAD_UNIVERSE, return_value=_universe_config()),
        patch(_REPO_PATH) as MockRepo,
        patch(_SERVICE_PATH) as MockService,
    ):
        repo_inst = MockRepo.return_value
        repo_inst.create_ingestion_run.return_value = run_mock
        service_inst = MockService.return_value
        service_inst.materialize.side_effect = RuntimeError("boom")

        ctx = build_asset_context()
        with pytest.raises(RuntimeError, match="boom"):
            candidate_universe(ctx, postgres=pg)

    repo_inst.fail_ingestion_run.assert_called_once()
    call_kwargs = repo_inst.fail_ingestion_run.call_args
    assert "boom" in call_kwargs.kwargs["error"]
