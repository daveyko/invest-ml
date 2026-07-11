"""Feature engineering Dagster assets."""

from dagster import AssetExecutionContext, asset

from invest_ml.defs.resources import PostgresResource


@asset(
    group_name="ml",
    description=(
        "Sync individual FeatureDefinition and FeatureSetDefinition rows from the YAML config "
        "to the database.  Must run before feature_snapshots."
    ),
)
def feature_registry(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    """Register features from features_v1.yaml into feature_definitions and feature_set_definitions.

    Not yet implemented.
    """
    raise NotImplementedError(
        "TODO: call features.definitions.load_feature_definitions, upsert to DB"
    )


@asset(
    group_name="ml",
    deps=["canonical_metrics", "price_bars", "feature_registry"],
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
