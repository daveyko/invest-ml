"""YAML configuration loaders.

All config files live under the project-root configs/ directory.
Loaders return plain dicts; callers are responsible for validation.
"""

from pathlib import Path
from typing import Any

import yaml

# Project root is four levels up from this file:
# src/invest_ml/config/loaders.py → src/invest_ml/config → src/invest_ml → src → project root
_CONFIGS_DIR = Path(__file__).parent.parent.parent.parent / "configs"


def _load_yaml(filename: str) -> dict[str, Any]:
    path = _CONFIGS_DIR / filename
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def load_sic_buckets(version: str = "v1") -> dict[str, Any]:
    return _load_yaml(f"sic_buckets_{version}.yaml")


def load_universe_config(version: str = "v1") -> dict[str, Any]:
    return _load_yaml(f"universe_{version}.yaml")


def load_canonical_metrics(version: str = "v1") -> dict[str, Any]:
    return _load_yaml(f"canonical_metrics_{version}.yaml")


def load_features_config(version: str = "v1") -> dict[str, Any]:
    return _load_yaml(f"features_{version}.yaml")


def load_target_spec(version: str = "v1") -> dict[str, Any]:
    return _load_yaml(f"target_{version}.yaml")


def load_market_data_config(version: str = "v1") -> dict[str, Any]:
    return _load_yaml(f"market_data_{version}.yaml")


def load_training_universe_config(version: str = "v1") -> dict[str, Any]:
    """Load the monthly-partitioned training universe config from configs/universes/."""
    return _load_yaml(f"universes/training_universe_{version}.yaml")


def load_feature_registry_config(name: str, version: str) -> dict[str, Any]:
    """Load a versioned feature registry config from configs/features/<name>_<version>.yaml."""
    return _load_yaml(f"features/{name}_{version}.yaml")
