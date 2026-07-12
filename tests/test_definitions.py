"""Verify that the Dagster Definitions object loads without errors and contains
the expected assets, jobs, and schedules.

No database connections or network requests are made.
"""

import pytest


def test_defs_imports() -> None:
    from invest_ml.definitions import defs

    assert defs is not None


def test_expected_assets_present() -> None:
    from invest_ml.definitions import defs

    expected = {
        "company_catalog",
        "companyfacts_data_profiles",
        "candidate_universe",
        "company_market_profiles",
        "training_universe",
        "scoring_universe",
        "xbrl_facts",
        "canonical_metrics",
        "price_bars",
        "feature_registry",
        "feature_snapshots",
        "matured_labels",
        "training_dataset",
        "trained_model",
        "current_predictions",
    }
    asset_keys = {key.to_user_string() for key in defs.resolve_all_asset_keys()}
    missing = expected - asset_keys
    assert not missing, f"Missing asset keys: {missing}"


def test_expected_jobs_present() -> None:
    from invest_ml.definitions import defs

    expected = {
        "sec_discovery_job",
        "selected_financials_job",
        "market_refresh_job",
        "feature_scoring_job",
        "model_training_job",
    }
    job_names = {job.name for job in (defs.jobs or [])}
    missing = expected - job_names
    assert not missing, f"Missing jobs: {missing}"


def test_expected_schedules_present() -> None:
    from invest_ml.definitions import defs

    expected = {
        "sec_discovery_schedule",
        "selected_financials_schedule",
        "market_refresh_schedule",
        "feature_scoring_schedule",
        "model_training_schedule",
    }
    schedule_names = {s.name for s in (defs.schedules or [])}
    missing = expected - schedule_names
    assert not missing, f"Missing schedules: {missing}"


def test_expected_resources_registered() -> None:
    from invest_ml.definitions import defs

    resource_keys = set(defs.resources or {})
    assert "postgres" in resource_keys
    assert "sec_bulk" in resource_keys
    assert "artifact_store" in resource_keys
