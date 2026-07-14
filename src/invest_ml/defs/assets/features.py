"""Feature engineering Dagster assets."""

import logging
import time

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from invest_ml.defs.resources import PostgresResource

logger = logging.getLogger(__name__)

_REGISTRY_CONFIG_NAME = "compounder"
_REGISTRY_CONFIG_VERSION = "v1"
_REGISTRY_SOURCE_URI = "configs/features/compounder_v1.yaml"


@asset(
    group_name="ml_features",
    description=(
        "Materialize versioned feature definitions and feature-set membership metadata "
        "from configs/features/compounder_v1.yaml. "
        "Does not calculate or persist feature values. "
        "Definitions are immutable: same version + different hash raises an error."
    ),
)
def feature_registry(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> MaterializeResult:
    """Register features from compounder_v1.yaml into the database.

    Flow
    ----
    1. Load configs/features/compounder_v1.yaml.
    2. Validate source references (canonical metrics, price-bar columns, operations).
    3. Calculate deterministic configuration hashes.
    4. Persist immutable feature definitions.
    5. Persist feature-set definition and ordered members.
    6. Mark ingestion run succeeded.
    7. On failure: mark ingestion run failed, re-raise.
    """
    from invest_ml.config.loaders import load_canonical_metrics, load_feature_registry_config
    from invest_ml.features.config import parse_feature_registry_config
    from invest_ml.features.registry_service import FeatureRegistryService
    from invest_ml.features.validator import validate_registry

    t_start = time.monotonic()

    # ── 1. Load configuration ──────────────────────────────────────────────────
    raw = load_feature_registry_config(_REGISTRY_CONFIG_NAME, _REGISTRY_CONFIG_VERSION)
    context.log.info(
        "Loaded feature registry config: registry_version=%s features=%d feature_sets=%d",
        raw.get("registry_version", "?"),
        len(raw.get("features") or {}),
        len(raw.get("feature_sets") or {}),
    )

    # ── 2. Validate ────────────────────────────────────────────────────────────
    canonical_cfg = load_canonical_metrics()
    known_metrics: set[str] = set((canonical_cfg.get("metrics") or {}).keys())

    validate_registry(raw, known_canonical_metrics=known_metrics)
    context.log.info("Feature registry validation passed")

    # ── 3. Parse into typed config with deterministic hashes ──────────────────
    registry_config = parse_feature_registry_config(raw)
    context.log.info(
        "Registry configuration hash: %.16s...", registry_config.configuration_hash
    )

    # ── 4. Materialize via service ─────────────────────────────────────────────
    session_factory = postgres.get_session_factory()
    service = FeatureRegistryService(session_factory=session_factory)

    result = service.materialize(
        registry_config=registry_config,
        source_uri=_REGISTRY_SOURCE_URI,
    )

    duration = time.monotonic() - t_start
    context.log.info(
        "feature_registry complete: features_created=%d features_already_present=%d "
        "feature_sets_created=%d members_created=%d duration=%.1fs",
        result.features_created,
        result.features_already_present,
        result.feature_sets_created,
        result.feature_set_members_created,
        duration,
    )

    # Categorize features for metadata
    features_by_category: dict[str, int] = {}
    for fc in registry_config.features:
        features_by_category[fc.category] = features_by_category.get(fc.category, 0) + 1

    fundamental_count = sum(
        v for k, v in features_by_category.items()
        if k.startswith("fundamental")
    )
    price_count = sum(
        v for k, v in features_by_category.items()
        if k in ("momentum", "volatility_and_risk", "liquidity")
    )
    categorical_count = features_by_category.get("categorical", 0)

    active_fs = registry_config.feature_sets[0] if registry_config.feature_sets else None

    return MaterializeResult(
        metadata={
            "registry_version": MetadataValue.text(result.registry_version),
            "registry_configuration_hash": MetadataValue.text(
                result.configuration_hash[:16] + "..."
            ),
            "features_configured": MetadataValue.int(result.features_configured),
            "features_created": MetadataValue.int(result.features_created),
            "features_already_present": MetadataValue.int(result.features_already_present),
            "feature_sets_configured": MetadataValue.int(result.feature_sets_configured),
            "feature_sets_created": MetadataValue.int(result.feature_sets_created),
            "feature_sets_already_present": MetadataValue.int(
                result.feature_sets_already_present
            ),
            "feature_set_members_created": MetadataValue.int(result.feature_set_members_created),
            "feature_set_members_already_present": MetadataValue.int(
                result.feature_set_members_already_present
            ),
            "active_feature_set_name": MetadataValue.text(
                active_fs.name if active_fs else ""
            ),
            "active_feature_set_version": MetadataValue.text(
                active_fs.version if active_fs else ""
            ),
            "fundamental_feature_count": MetadataValue.int(fundamental_count),
            "price_feature_count": MetadataValue.int(price_count),
            "categorical_feature_count": MetadataValue.int(categorical_count),
            "duration_seconds": MetadataValue.float(round(duration, 1)),
        }
    )


@asset(
    group_name="ml_features",
    deps=["canonical_metrics", "selected_price_bars", "feature_registry", "training_universe"],
    description=(
        "Immutable computed feature vectors for all training/scoring universe members. "
        "If upstream data changes, a new snapshot is inserted (never updated). "
        "All inputs must satisfy available_at <= as_of_date."
    ),
)
def feature_snapshots(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    """Build feature snapshots for each company/as-of-date pair in the universe.

    Not yet implemented.
    """
    raise NotImplementedError(
        "TODO: iterate universe members x as-of-dates, call features.builder.build_snapshot"
    )
