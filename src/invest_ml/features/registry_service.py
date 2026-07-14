"""Feature registry materialization service.

Persists versioned, immutable feature definitions, feature-set definitions,
and ordered feature-set members to the database.

Does NOT:
- Calculate feature values.
- Create feature snapshots.
- Query canonical metrics rows.
- Query price-bar rows.
- Call external APIs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from invest_ml.features.config import (
    FeatureRegistryConfig,
    compute_feature_set_hash,
)

logger = logging.getLogger(__name__)

_FEATURE_REGISTRY_SOURCE = "feature_registry"


@dataclass(frozen=True)
class FeatureRegistryMaterializationResult:
    registry_version: str

    features_configured: int
    features_created: int
    features_already_present: int

    feature_sets_configured: int
    feature_sets_created: int
    feature_sets_already_present: int

    feature_set_members_created: int
    feature_set_members_already_present: int

    configuration_hash: str


class FeatureRegistryService:
    def __init__(self, session_factory) -> None:  # type: ignore[type-arg]
        self._session_factory = session_factory

    def materialize(
        self,
        *,
        registry_config: FeatureRegistryConfig,
        source_uri: str,
        ingestion_run_id: UUID | None = None,
    ) -> FeatureRegistryMaterializationResult:
        """Persist the validated registry to the database.

        Uses one transaction for all writes.  Rolls back on any error.
        """
        from invest_ml.db.repositories.feature_registry import FeatureRegistryRepository

        features_created = 0
        features_already_present = 0
        feature_sets_created = 0
        feature_sets_already_present = 0
        members_created = 0
        members_already_present = 0

        with self._session_factory() as session:
            repo = FeatureRegistryRepository(session)

            # Create or reuse ingestion run
            if ingestion_run_id is None:
                run = repo.create_ingestion_run(
                    source=_FEATURE_REGISTRY_SOURCE,
                    source_uri=source_uri,
                    started_at=datetime.now(tz=UTC),
                )
                session.flush()
                ingestion_run_id = run.run_id

            try:
                # ── 1. Persist feature definitions ────────────────────────────
                feature_id_map: dict[str, UUID] = {}
                now = datetime.now(tz=UTC)

                for fc in registry_config.features:
                    existing = repo.find_feature_definition(fc.name, fc.version)
                    if existing is not None:
                        if existing.configuration_hash != fc.configuration_hash:
                            raise ValueError(
                                f"Immutable-version conflict: feature '{fc.name}:{fc.version}' "
                                f"already exists with a different configuration hash. "
                                f"DB hash={existing.configuration_hash!r}, "
                                f"config hash={fc.configuration_hash!r}. "
                                f"Bump the feature version to create a new definition."
                            )
                        feature_id_map[fc.name] = existing.feature_definition_id
                        features_already_present += 1
                    else:
                        fd = repo.create_feature_definition(
                            {
                                "feature_name": fc.name,
                                "feature_version": fc.version,
                                "category": fc.category,
                                "entity_grain": fc.entity_grain,
                                "value_type": fc.value_type,
                                "description": fc.description,
                                "definition": fc.definition,
                                "point_in_time_policy": fc.point_in_time_policy,
                                "missing_value_policy": fc.missing_value_policy,
                                "configuration_hash": fc.configuration_hash,
                                "created_at": now,
                            }
                        )
                        feature_id_map[fc.name] = fd.feature_definition_id
                        features_created += 1
                        logger.info("Created feature definition: %s:%s", fc.name, fc.version)

                # ── 2. Persist feature set definitions ────────────────────────
                feature_set_id_map: dict[str, UUID] = {}

                for fsc in registry_config.feature_sets:
                    members_for_hash = [
                        (fn, fc.version, fc.configuration_hash)
                        for fn in fsc.feature_names
                        for fc in [next(f for f in registry_config.features if f.name == fn)]
                    ]
                    cfg_hash = compute_feature_set_hash(
                        fsc.name,
                        fsc.version,
                        fsc.entity_grain,
                        fsc.snapshot_frequency,
                        members_for_hash,
                    )

                    existing_fs = repo.find_feature_set(fsc.name, fsc.version)
                    if existing_fs is not None:
                        if existing_fs.configuration_hash != cfg_hash:
                            raise ValueError(
                                f"Immutable-version conflict: feature set '{fsc.name}:{fsc.version}' "
                                f"already exists with a different configuration hash. "
                                f"DB hash={existing_fs.configuration_hash!r}, "
                                f"config hash={cfg_hash!r}. "
                                f"Create a new feature set version."
                            )
                        feature_set_id_map[fsc.version] = existing_fs.feature_set_id
                        feature_sets_already_present += 1
                    else:
                        fsd = repo.create_feature_set(
                            {
                                "name": fsc.name,
                                "version": fsc.version,
                                "entity_grain": fsc.entity_grain,
                                "snapshot_frequency": fsc.snapshot_frequency,
                                "description": fsc.description,
                                "status": "active",
                                "configuration_hash": cfg_hash,
                                "created_at": now,
                            }
                        )
                        feature_set_id_map[fsc.version] = fsd.feature_set_id
                        feature_sets_created += 1
                        logger.info(
                            "Created feature set: %s:%s (%d members)",
                            fsc.name, fsc.version, len(fsc.feature_names),
                        )

                # ── 3. Persist feature set members ────────────────────────────
                for fsc in registry_config.feature_sets:
                    fs_id = feature_set_id_map[fsc.version]
                    existing_member_count = repo.count_feature_set_members(fs_id)
                    if existing_member_count > 0:
                        members_already_present += existing_member_count
                    else:
                        member_rows = [
                            {
                                "feature_set_id": fs_id,
                                "feature_definition_id": feature_id_map[fn],
                                "ordinal": ordinal,
                                "enabled": True,
                            }
                            for ordinal, fn in enumerate(fsc.feature_names)
                        ]
                        created = repo.create_feature_set_members(member_rows)
                        members_created += created

                # ── 4. Mark run succeeded ─────────────────────────────────────
                repo.succeed_ingestion_run(
                    ingestion_run_id,
                    entities_checked=len(registry_config.features),
                    entities_changed=features_created + feature_sets_created + members_created,
                    extra_metadata={
                        "registry_version": registry_config.registry_version,
                        "configuration_hash": registry_config.configuration_hash,
                        "features_created": features_created,
                        "feature_sets_created": feature_sets_created,
                        "members_created": members_created,
                    },
                )
                session.commit()

            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                logger.error("feature_registry materialization failed: %s", error_text)
                try:
                    repo.fail_ingestion_run(ingestion_run_id, error=error_text)
                    session.commit()
                except Exception:
                    logger.exception("Could not mark IngestionRun %s as failed", ingestion_run_id)
                raise

        return FeatureRegistryMaterializationResult(
            registry_version=registry_config.registry_version,
            features_configured=len(registry_config.features),
            features_created=features_created,
            features_already_present=features_already_present,
            feature_sets_configured=len(registry_config.feature_sets),
            feature_sets_created=feature_sets_created,
            feature_sets_already_present=feature_sets_already_present,
            feature_set_members_created=members_created,
            feature_set_members_already_present=members_already_present,
            configuration_hash=registry_config.configuration_hash,
        )
