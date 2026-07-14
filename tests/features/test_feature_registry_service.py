"""Persistence tests for FeatureRegistryService.

All DB calls are mocked — no live database required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from invest_ml.config.loaders import load_canonical_metrics, load_feature_registry_config
from invest_ml.features.config import parse_feature_registry_config
from invest_ml.features.registry_service import FeatureRegistryService

_REGISTRY_SOURCE_URI = "configs/features/compounder_v1.yaml"
_REPO_PATH = "invest_ml.db.repositories.feature_registry.FeatureRegistryRepository"


def _load_registry():
    raw = load_feature_registry_config("compounder", "v1")
    return parse_feature_registry_config(raw)


def _known_metrics() -> set[str]:
    cfg = load_canonical_metrics()
    return set((cfg.get("metrics") or {}).keys())


def _make_session_factory(repo_mock):
    session_mock = MagicMock()
    session_mock.__enter__ = MagicMock(return_value=session_mock)
    session_mock.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=session_mock)


# ── First-run: creates all rows ────────────────────────────────────────────────


def test_first_run_creates_all_feature_definitions():
    registry_config = _load_registry()

    with patch(
        "invest_ml.db.repositories.feature_registry.FeatureRegistryRepository"
    ) as MockRepo:
        repo_inst = MockRepo.return_value
        # Ingestion run
        run_mock = MagicMock()
        run_mock.run_id = uuid4()
        repo_inst.create_ingestion_run.return_value = run_mock
        # No existing features
        repo_inst.find_feature_definition.return_value = None
        # Create returns a mock with an ID
        def _create_fd(row):
            fd = MagicMock()
            fd.feature_definition_id = uuid4()
            return fd
        repo_inst.create_feature_definition.side_effect = _create_fd
        # No existing feature sets
        repo_inst.find_feature_set.return_value = None
        def _create_fsd(row):
            fsd = MagicMock()
            fsd.feature_set_id = uuid4()
            return fsd
        repo_inst.create_feature_set.side_effect = _create_fsd
        repo_inst.count_feature_set_members.return_value = 0
        repo_inst.create_feature_set_members.return_value = 32

        session_factory = MagicMock()
        session_ctx = MagicMock()
        session_ctx.__enter__ = MagicMock(return_value=MagicMock())
        session_ctx.__exit__ = MagicMock(return_value=False)
        session_factory.return_value = session_ctx

        service = FeatureRegistryService(session_factory=session_factory)
        result = service.materialize(
            registry_config=registry_config,
            source_uri=_REGISTRY_SOURCE_URI,
        )

    assert result.features_configured == 32
    assert result.features_created == 32
    assert result.features_already_present == 0
    assert result.feature_sets_configured == 1
    assert result.feature_sets_created == 1
    assert result.feature_sets_already_present == 0
    assert result.feature_set_members_created == 32
    assert result.feature_set_members_already_present == 0


def test_identical_rerun_is_noop():
    registry_config = _load_registry()

    with patch(
        "invest_ml.db.repositories.feature_registry.FeatureRegistryRepository"
    ) as MockRepo:
        repo_inst = MockRepo.return_value
        run_mock = MagicMock()
        run_mock.run_id = uuid4()
        repo_inst.create_ingestion_run.return_value = run_mock

        # All features already exist with same hash
        def _find_fd(name, version):
            fd = MagicMock()
            fc = next(f for f in registry_config.features if f.name == name)
            fd.configuration_hash = fc.configuration_hash
            fd.feature_definition_id = uuid4()
            return fd
        repo_inst.find_feature_definition.side_effect = _find_fd

        # Feature set already exists with same hash
        fsd_mock = MagicMock()
        from invest_ml.features.config import compute_feature_set_hash
        fc_map = {f.name: f for f in registry_config.features}
        fsc = registry_config.feature_sets[0]
        members = [(fn, fc_map[fn].version, fc_map[fn].configuration_hash) for fn in fsc.feature_names]
        fsd_mock.configuration_hash = compute_feature_set_hash(
            fsc.name, fsc.version, fsc.entity_grain, fsc.snapshot_frequency, members
        )
        fsd_mock.feature_set_id = uuid4()
        repo_inst.find_feature_set.return_value = fsd_mock

        # Members already exist
        repo_inst.count_feature_set_members.return_value = 32

        session_factory = MagicMock()
        session_ctx = MagicMock()
        session_ctx.__enter__ = MagicMock(return_value=MagicMock())
        session_ctx.__exit__ = MagicMock(return_value=False)
        session_factory.return_value = session_ctx

        service = FeatureRegistryService(session_factory=session_factory)
        result = service.materialize(
            registry_config=registry_config,
            source_uri=_REGISTRY_SOURCE_URI,
        )

    assert result.features_created == 0
    assert result.features_already_present == 32
    assert result.feature_sets_created == 0
    assert result.feature_sets_already_present == 1
    assert result.feature_set_members_created == 0
    assert result.feature_set_members_already_present == 32
    # No new definitions or sets were created
    repo_inst.create_feature_definition.assert_not_called()
    repo_inst.create_feature_set.assert_not_called()
    repo_inst.create_feature_set_members.assert_not_called()


def test_same_version_different_hash_raises():
    registry_config = _load_registry()

    with patch(
        "invest_ml.db.repositories.feature_registry.FeatureRegistryRepository"
    ) as MockRepo:
        repo_inst = MockRepo.return_value
        run_mock = MagicMock()
        run_mock.run_id = uuid4()
        repo_inst.create_ingestion_run.return_value = run_mock

        # Simulate a feature that exists with a DIFFERENT hash
        def _find_fd(name, version):
            fd = MagicMock()
            fd.configuration_hash = "aaaaaaaaaaaaaaaaaa_different_hash"
            fd.feature_definition_id = uuid4()
            return fd
        repo_inst.find_feature_definition.side_effect = _find_fd

        session_factory = MagicMock()
        session_ctx = MagicMock()
        session_ctx.__enter__ = MagicMock(return_value=MagicMock())
        session_ctx.__exit__ = MagicMock(return_value=False)
        session_factory.return_value = session_ctx

        service = FeatureRegistryService(session_factory=session_factory)
        with pytest.raises(ValueError, match="Immutable-version conflict"):
            service.materialize(
                registry_config=registry_config,
                source_uri=_REGISTRY_SOURCE_URI,
            )

    # On failure, ingestion run must be marked failed
    repo_inst.fail_ingestion_run.assert_called_once()


def test_version_conflict_no_feature_set_created():
    """After a feature conflict, no feature set must be created."""
    registry_config = _load_registry()

    with patch(
        "invest_ml.db.repositories.feature_registry.FeatureRegistryRepository"
    ) as MockRepo:
        repo_inst = MockRepo.return_value
        run_mock = MagicMock()
        run_mock.run_id = uuid4()
        repo_inst.create_ingestion_run.return_value = run_mock

        call_count = [0]

        def _find_fd(name, version):
            call_count[0] += 1
            # First 5 return None (would be created), then return conflict
            if call_count[0] <= 5:
                return None
            fd = MagicMock()
            fd.configuration_hash = "different_conflicting_hash"
            fd.feature_definition_id = uuid4()
            return fd

        repo_inst.find_feature_definition.side_effect = _find_fd

        def _create_fd(row):
            fd = MagicMock()
            fd.feature_definition_id = uuid4()
            return fd
        repo_inst.create_feature_definition.side_effect = _create_fd

        session_factory = MagicMock()
        session_ctx = MagicMock()
        session_ctx.__enter__ = MagicMock(return_value=MagicMock())
        session_ctx.__exit__ = MagicMock(return_value=False)
        session_factory.return_value = session_ctx

        service = FeatureRegistryService(session_factory=session_factory)
        with pytest.raises(ValueError, match="Immutable-version conflict"):
            service.materialize(
                registry_config=registry_config,
                source_uri=_REGISTRY_SOURCE_URI,
            )

    # Feature set must never be created when a feature conflict occurred
    repo_inst.create_feature_set.assert_not_called()
    repo_inst.create_feature_set_members.assert_not_called()
    # Ingestion run must be marked failed
    repo_inst.fail_ingestion_run.assert_called_once()


def test_member_ordering_preserved():
    """Feature set members must be inserted in YAML declaration order."""
    registry_config = _load_registry()

    captured_members: list[dict] = []

    with patch(
        "invest_ml.db.repositories.feature_registry.FeatureRegistryRepository"
    ) as MockRepo:
        repo_inst = MockRepo.return_value
        run_mock = MagicMock()
        run_mock.run_id = uuid4()
        repo_inst.create_ingestion_run.return_value = run_mock
        repo_inst.find_feature_definition.return_value = None

        def _create_fd(row):
            fd = MagicMock()
            fd.feature_definition_id = uuid4()
            return fd
        repo_inst.create_feature_definition.side_effect = _create_fd
        repo_inst.find_feature_set.return_value = None

        def _create_fsd(row):
            fsd = MagicMock()
            fsd.feature_set_id = uuid4()
            return fsd
        repo_inst.create_feature_set.side_effect = _create_fsd
        repo_inst.count_feature_set_members.return_value = 0

        def _capture_members(members):
            captured_members.extend(members)
            return len(members)
        repo_inst.create_feature_set_members.side_effect = _capture_members

        session_factory = MagicMock()
        session_ctx = MagicMock()
        session_ctx.__enter__ = MagicMock(return_value=MagicMock())
        session_ctx.__exit__ = MagicMock(return_value=False)
        session_factory.return_value = session_ctx

        service = FeatureRegistryService(session_factory=session_factory)
        service.materialize(
            registry_config=registry_config,
            source_uri=_REGISTRY_SOURCE_URI,
        )

    assert len(captured_members) == 32
    # ordinals must be 0-based and sequential
    ordinals = [m["ordinal"] for m in captured_members]
    assert ordinals == list(range(32))
    # first member should be model_bucket (index 0 in compounder_v1)
    assert captured_members[0]["ordinal"] == 0


def test_registry_does_not_create_snapshots():
    """Materializing the registry must never create feature snapshot rows."""
    registry_config = _load_registry()

    with patch(
        "invest_ml.db.repositories.feature_registry.FeatureRegistryRepository"
    ) as MockRepo:
        repo_inst = MockRepo.return_value
        run_mock = MagicMock()
        run_mock.run_id = uuid4()
        repo_inst.create_ingestion_run.return_value = run_mock
        repo_inst.find_feature_definition.return_value = None

        def _create_fd(row):
            fd = MagicMock()
            fd.feature_definition_id = uuid4()
            return fd
        repo_inst.create_feature_definition.side_effect = _create_fd
        repo_inst.find_feature_set.return_value = None

        def _create_fsd(row):
            fsd = MagicMock()
            fsd.feature_set_id = uuid4()
            return fsd
        repo_inst.create_feature_set.side_effect = _create_fsd
        repo_inst.count_feature_set_members.return_value = 0
        repo_inst.create_feature_set_members.return_value = 32

        session_factory = MagicMock()
        session_ctx = MagicMock()
        session_ctx.__enter__ = MagicMock(return_value=MagicMock())
        session_ctx.__exit__ = MagicMock(return_value=False)
        session_factory.return_value = session_ctx

        service = FeatureRegistryService(session_factory=session_factory)
        service.materialize(
            registry_config=registry_config,
            source_uri=_REGISTRY_SOURCE_URI,
        )

    # FeatureSnapshot should never be referenced
    session = session_ctx.__enter__.return_value
    add_calls = session.add.call_args_list
    from invest_ml.db.models.features import FeatureSnapshot
    for c in add_calls:
        assert not isinstance(c.args[0], FeatureSnapshot), (
            "feature_registry materialization must not create FeatureSnapshot rows"
        )
