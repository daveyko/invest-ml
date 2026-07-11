"""Load and validate feature definitions from the YAML config.

A feature definition specifies:
  name, version, description, inputs, lookback, point_in_time_rule

A feature set specifies which exact (name, version) pairs form the input vector.
Changing any member's version requires a new feature set version.
"""

from invest_ml.config.loaders import load_features_config
from invest_ml.db.models.features import FeatureDefinition, FeatureSetDefinition
from invest_ml.utils import deterministic_hash


def load_feature_definitions(version: str = "v1") -> list[FeatureDefinition]:
    """Parse features YAML into unsaved FeatureDefinition rows.

    TODO: implement YAML → FeatureDefinition mapping and git SHA injection.
    """
    raise NotImplementedError("TODO: implement feature definition loading")


def load_feature_set_definition(version: str = "v1") -> FeatureSetDefinition:
    """Load the versioned feature set from YAML.

    TODO: implement members list, content_hash computation, and git SHA injection.
    """
    raise NotImplementedError("TODO: implement feature set definition loading")


def validate_feature_set_members(
    features_config: dict,
) -> None:
    """Assert that every member of the feature set references a declared individual feature.

    Raises ValueError on mismatch.
    """
    individual = {
        f["name"]: f["version"]
        for f in features_config.get("features", {}).values()
        if isinstance(f, dict) and "version" in f
    }
    for member in features_config.get("feature_set", {}).get("members", []):
        name = member.get("name")
        version = member.get("version")
        if name not in individual:
            raise ValueError(f"Feature set member '{name}' not found in features config")
        if individual[name] != version:
            raise ValueError(
                f"Feature set member '{name}' version '{version}' does not match "
                f"declared version '{individual[name]}'"
            )
