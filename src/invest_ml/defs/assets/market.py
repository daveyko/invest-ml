"""Market data Dagster asset.

price_bars is in the financial_warehouse group because it is a prerequisite
for both feature construction and label computation.
"""

from dagster import AssetExecutionContext, asset

from invest_ml.defs.resources import ArtifactStoreResource, PostgresResource


@asset(
    group_name="financial_warehouse",
    deps=["training_universe"],
    description=(
        "Daily adjusted price bars for all training-universe securities. "
        "Also fetched for scoring-universe securities that are not in the training universe."
    ),
)
def price_bars(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    artifact_store: ArtifactStoreResource,
) -> None:
    """Fetch and persist daily price bars from the market data provider.

    Not yet implemented.  Provider (e.g. yfinance, Polygon) not yet chosen.
    """
    raise NotImplementedError(
        "TODO: choose price provider, call market.client, bulk_insert_ignore into price_bars"
    )
