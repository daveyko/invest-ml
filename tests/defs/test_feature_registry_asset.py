"""Dagster asset tests for feature_registry.

No database connections, SEC calls, or Tiingo calls are made.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from dagster import build_asset_context

from invest_ml.defs.assets.features import feature_registry, feature_snapshots

_SERVICE_PATH = "invest_ml.features.registry_service.FeatureRegistryService"
_VALIDATOR_PATH = "invest_ml.features.validator.validate_registry"


def _make_postgres_resource() -> MagicMock:
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    resource = MagicMock()
    resource.get_session_factory.return_value = MagicMock(return_value=mock_session)
    return resource


def _mock_materialize_result():
    from invest_ml.features.registry_service import FeatureRegistryMaterializationResult
    return FeatureRegistryMaterializationResult(
        registry_version="feature_registry_v1",
        features_configured=32,
        features_created=32,
        features_already_present=0,
        feature_sets_configured=1,
        feature_sets_created=1,
        feature_sets_already_present=0,
        feature_set_members_created=32,
        feature_set_members_already_present=0,
        configuration_hash="abc" * 21 + "d",
    )


# ── Definition-load tests ──────────────────────────────────────────────────────


def test_feature_registry_asset_is_importable():
    """Definitions must load without network or DB activity."""
    assert feature_registry is not None
    assert feature_snapshots is not None


def test_feature_registry_group_name():
    from dagster import AssetKey
    key = AssetKey("feature_registry")
    assert feature_registry.group_names_by_key.get(key) == "ml_features"


def test_feature_snapshots_deps_include_feature_registry():
    from dagster import AssetKey
    own_key = AssetKey("feature_snapshots")
    all_deps = feature_snapshots.asset_deps.get(own_key, set())
    dep_strings = {k.to_user_string() for k in all_deps}
    assert "feature_registry" in dep_strings


def test_feature_registry_has_no_upstream_asset_deps():
    """feature_registry is a configuration-backed asset with no warehouse dependencies."""
    from dagster import AssetKey
    own_key = AssetKey("feature_registry")
    deps = feature_registry.asset_deps.get(own_key, set())
    assert len(deps) == 0, (
        f"feature_registry must have no warehouse dependencies. Found: {deps}"
    )


# ── Successful materialization ─────────────────────────────────────────────────


def test_successful_materialization_emits_required_metadata():
    pg = _make_postgres_resource()

    with patch(_SERVICE_PATH) as MockService:
        MockService.return_value.materialize.return_value = _mock_materialize_result()
        ctx = build_asset_context()
        result = feature_registry(ctx, postgres=pg)

    meta = result.metadata
    required_keys = {
        "registry_version",
        "registry_configuration_hash",
        "features_configured",
        "features_created",
        "features_already_present",
        "feature_sets_configured",
        "feature_sets_created",
        "feature_sets_already_present",
        "feature_set_members_created",
        "feature_set_members_already_present",
        "active_feature_set_name",
        "active_feature_set_version",
        "fundamental_feature_count",
        "price_feature_count",
        "categorical_feature_count",
        "duration_seconds",
    }
    missing = required_keys - set(meta.keys())
    assert not missing, f"Metadata keys missing: {missing}"


def test_successful_materialization_returns_materialize_result():
    from dagster import MaterializeResult

    pg = _make_postgres_resource()
    with patch(_SERVICE_PATH) as MockService:
        MockService.return_value.materialize.return_value = _mock_materialize_result()
        ctx = build_asset_context()
        result = feature_registry(ctx, postgres=pg)

    assert isinstance(result, MaterializeResult)


def test_materialization_counts_in_metadata():
    pg = _make_postgres_resource()
    with patch(_SERVICE_PATH) as MockService:
        MockService.return_value.materialize.return_value = _mock_materialize_result()
        ctx = build_asset_context()
        result = feature_registry(ctx, postgres=pg)

    assert int(result.metadata["features_configured"].value) == 32
    assert int(result.metadata["feature_sets_configured"].value) == 1


# ── No external API calls ──────────────────────────────────────────────────────


def test_no_sec_calls_during_materialization():
    """feature_registry must not import or call any SEC module."""
    pre_modules = set(k for k in sys.modules if "invest_ml.sec" in k)

    pg = _make_postgres_resource()
    with patch(_SERVICE_PATH) as MockService:
        MockService.return_value.materialize.return_value = _mock_materialize_result()
        ctx = build_asset_context()
        feature_registry(ctx, postgres=pg)

    post_modules = set(k for k in sys.modules if "invest_ml.sec" in k)
    new_sec_imports = post_modules - pre_modules
    assert not new_sec_imports, f"SEC modules were imported: {new_sec_imports}"


def test_no_tiingo_calls_during_materialization():
    """feature_registry must not import or call any Tiingo provider module."""
    pre_modules = set(k for k in sys.modules if "tiingo" in k)

    pg = _make_postgres_resource()
    with patch(_SERVICE_PATH) as MockService:
        MockService.return_value.materialize.return_value = _mock_materialize_result()
        ctx = build_asset_context()
        feature_registry(ctx, postgres=pg)

    post_modules = set(k for k in sys.modules if "tiingo" in k)
    new_tiingo = post_modules - pre_modules
    assert not new_tiingo, f"Tiingo modules were imported: {new_tiingo}"


# ── Failed materialization ─────────────────────────────────────────────────────


def test_failed_materialization_reraises():
    pg = _make_postgres_resource()
    with patch(_SERVICE_PATH) as MockService:
        MockService.return_value.materialize.side_effect = ValueError("Test failure")
        ctx = build_asset_context()
        with pytest.raises(ValueError, match="Test failure"):
            feature_registry(ctx, postgres=pg)


def test_validation_failure_does_not_call_service():
    """If validation fails, the service must not be called."""
    pg = _make_postgres_resource()
    with patch(_SERVICE_PATH) as MockService:
        with patch(_VALIDATOR_PATH, side_effect=Exception("validation error")):
            ctx = build_asset_context()
            with pytest.raises(Exception, match="validation error"):
                feature_registry(ctx, postgres=pg)
    MockService.return_value.materialize.assert_not_called()
