"""Typed configuration models for the feature registry YAML.

Parses configs/features/<name>_<version>.yaml into structured dataclasses
and computes deterministic configuration hashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from invest_ml.utils import deterministic_hash


@dataclass(frozen=True)
class FeatureConfig:
    name: str
    version: str
    category: str
    entity_grain: str
    value_type: str
    description: str
    definition: dict[str, Any]
    point_in_time_policy: dict[str, Any]
    missing_value_policy: dict[str, Any]
    configuration_hash: str


@dataclass(frozen=True)
class FeatureSetConfig:
    name: str
    version: str
    entity_grain: str
    snapshot_frequency: str
    description: str
    feature_names: tuple[str, ...]


@dataclass(frozen=True)
class FeatureRegistryConfig:
    registry_version: str
    feature_sets: tuple[FeatureSetConfig, ...]
    features: tuple[FeatureConfig, ...]
    configuration_hash: str


def compute_feature_hash(
    name: str,
    version: str,
    definition: dict[str, Any],
    point_in_time_policy: dict[str, Any],
    missing_value_policy: dict[str, Any],
) -> str:
    return deterministic_hash(
        {
            "feature_name": name,
            "feature_version": version,
            "definition": definition,
            "point_in_time_policy": point_in_time_policy,
            "missing_value_policy": missing_value_policy,
        }
    )


def compute_feature_set_hash(
    name: str,
    version: str,
    entity_grain: str,
    snapshot_frequency: str,
    members: list[tuple[str, str, str]],
) -> str:
    """Hash over ordered feature members so reordering changes the hash."""
    return deterministic_hash(
        {
            "feature_set_name": name,
            "feature_set_version": version,
            "entity_grain": entity_grain,
            "snapshot_frequency": snapshot_frequency,
            "members": [
                {"name": n, "version": v, "configuration_hash": h}
                for n, v, h in members
            ],
        }
    )


def parse_feature_registry_config(raw: dict[str, Any]) -> FeatureRegistryConfig:
    """Parse a raw YAML dict into a FeatureRegistryConfig with computed hashes."""
    registry_version = raw.get("registry_version", "")
    if not registry_version:
        raise ValueError("registry_version is required")

    raw_features: dict[str, Any] = raw.get("features") or {}
    raw_feature_sets: dict[str, Any] = raw.get("feature_sets") or {}

    feature_map: dict[str, FeatureConfig] = {}
    for fname, fspec in raw_features.items():
        if not isinstance(fspec, dict):
            raise ValueError(f"Feature '{fname}' must be a mapping")
        version = fspec.get("version", "")
        if not version:
            raise ValueError(f"Feature '{fname}' is missing 'version'")
        definition = fspec.get("definition") or {}
        pit = fspec.get("point_in_time_policy") or {}
        mvp = fspec.get("missing_value_policy") or {}
        cfg_hash = compute_feature_hash(fname, version, definition, pit, mvp)
        feature_map[fname] = FeatureConfig(
            name=fname,
            version=version,
            category=fspec.get("category", ""),
            entity_grain=fspec.get("entity_grain", "company_security"),
            value_type=fspec.get("value_type", "float"),
            description=fspec.get("description", ""),
            definition=definition,
            point_in_time_policy=pit,
            missing_value_policy=mvp,
            configuration_hash=cfg_hash,
        )

    feature_set_configs: list[FeatureSetConfig] = []
    for fs_key, fsspec in raw_feature_sets.items():
        if not isinstance(fsspec, dict):
            raise ValueError(f"Feature set '{fs_key}' must be a mapping")
        fs_name = fsspec.get("name", fs_key)
        fs_version = fsspec.get("version", "")
        if not fs_version:
            raise ValueError(f"Feature set '{fs_key}' is missing 'version'")
        feature_names: list[str] = fsspec.get("features") or []

        members_for_hash = []
        for fn in feature_names:
            if fn not in feature_map:
                raise ValueError(
                    f"Feature set '{fs_key}' references unknown feature '{fn}'"
                )
            fc = feature_map[fn]
            members_for_hash.append((fn, fc.version, fc.configuration_hash))

        feature_set_configs.append(
            FeatureSetConfig(
                name=fs_name,
                version=fs_version,
                entity_grain=fsspec.get("entity_grain", "company_security"),
                snapshot_frequency=fsspec.get("snapshot_frequency", "monthly"),
                description=fsspec.get("description", ""),
                feature_names=tuple(feature_names),
            )
        )

    registry_hash = deterministic_hash(
        {
            "registry_version": registry_version,
            "feature_sets": {
                fsc.version: {
                    "name": fsc.name,
                    "version": fsc.version,
                    "entity_grain": fsc.entity_grain,
                    "snapshot_frequency": fsc.snapshot_frequency,
                    "features": list(fsc.feature_names),
                }
                for fsc in feature_set_configs
            },
            "features": {
                fname: {
                    "version": fc.version,
                    "configuration_hash": fc.configuration_hash,
                }
                for fname, fc in feature_map.items()
            },
        }
    )

    return FeatureRegistryConfig(
        registry_version=registry_version,
        feature_sets=tuple(feature_set_configs),
        features=tuple(feature_map.values()),
        configuration_hash=registry_hash,
    )
