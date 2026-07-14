"""ML modeling Dagster assets."""

from dagster import AssetExecutionContext, asset

from invest_ml.defs.resources import ArtifactStoreResource, PostgresResource


@asset(
    group_name="ml",
    deps=["feature_snapshots", "selected_price_bars"],
    description=(
        "Realized labels for feature snapshots whose horizon has elapsed. "
        "Labels intentionally use future price data; feature construction must not."
    ),
)
def matured_labels(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    """Compute labels for snapshots where end_trading_date <= today.

    Not yet implemented.
    """
    raise NotImplementedError(
        "TODO: call modeling.labels.compute_label for each eligible snapshot"
    )


@asset(
    group_name="ml",
    deps=["feature_snapshots", "matured_labels", "training_universe"],
    description=(
        "Logical immutable dataset: the paired set of feature snapshots and labels. "
        "Parquet artifact is optional; the DB rows are the source of truth."
    ),
)
def training_dataset(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    artifact_store: ArtifactStoreResource,
) -> None:
    """Build and persist the training dataset definition and row membership.

    Not yet implemented.
    """
    raise NotImplementedError(
        "TODO: call modeling.dataset.build_dataset, persist TrainingDataset + rows"
    )


@asset(
    group_name="ml",
    deps=["training_dataset"],
    description="Train and persist a model artifact from the latest training dataset.",
)
def trained_model(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    artifact_store: ArtifactStoreResource,
) -> None:
    """Train a model and record it as a candidate ModelRun.

    Not yet implemented.  ML algorithm not yet chosen.
    """
    raise NotImplementedError(
        "TODO: call modeling.trainer.train_model, record ModelRun with status='candidate'"
    )


@asset(
    group_name="ml",
    deps=["trained_model", "feature_snapshots", "scoring_universe"],
    description=(
        "Latest predictions from the promoted model for all scoring-universe members. "
        "Each prediction references an immutable feature snapshot."
    ),
)
def current_predictions(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    """Score the scoring universe with the promoted model.

    Not yet implemented.
    """
    raise NotImplementedError(
        "TODO: load promoted model, call modeling.scorer.score_universe, bulk_insert predictions"
    )
