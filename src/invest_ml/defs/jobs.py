"""Dagster job definitions.

Each job is a named subset of the asset graph.  Jobs can be run ad-hoc from
the Dagster UI or triggered by a schedule.
"""

from dagster import AssetSelection, define_asset_job

sec_discovery_job = define_asset_job(
    name="sec_discovery_job",
    selection=AssetSelection.assets(
        "company_catalog",
        "companyfacts_data_profiles",
        "candidate_universe",
    ),
    description="Download SEC submissions, build company catalog, profile CompanyFacts, build candidate universe.",
)

selected_financials_job = define_asset_job(
    name="selected_financials_job",
    selection=AssetSelection.assets(
        "xbrl_facts",
        "canonical_metrics",
    ),
    description="Download and flatten CompanyFacts for training-universe members, then normalize into canonical metrics.",
)

market_refresh_job = define_asset_job(
    name="market_refresh_job",
    selection=AssetSelection.assets(
        "company_market_profiles",
        "training_universe",
        "scoring_universe",
        "selected_price_bars",
    ),
    description="Refresh market profiles, rebuild universe gates, ingest daily price bars.",
)

feature_scoring_job = define_asset_job(
    name="feature_scoring_job",
    selection=AssetSelection.assets(
        "feature_registry",
        "feature_snapshots",
        "current_predictions",
    ),
    description="Sync feature definitions, build snapshots, score the scoring universe.",
)

model_training_job = define_asset_job(
    name="model_training_job",
    selection=AssetSelection.assets(
        "matured_labels",
        "training_dataset",
        "trained_model",
    ),
    description="Compute matured labels, assemble dataset, train and record a model candidate.",
)

all_jobs = [
    sec_discovery_job,
    selected_financials_job,
    market_refresh_job,
    feature_scoring_job,
    model_training_job,
]
