"""Dagster Definitions entry point for invest_ml.

All assets, jobs, schedules, and resources are registered here.
No database connections or network requests occur at import time.
"""

from dagster import Definitions, EnvVar

from invest_ml.defs.assets.discovery import (
    candidate_universe,
    companyfacts_data_profiles,
    company_catalog,
    company_market_profiles,
    scoring_universe,
    training_universe,
)
from invest_ml.defs.assets.features import feature_registry, feature_snapshots
from invest_ml.defs.assets.financials import canonical_metrics, selected_companyfacts_raw, xbrl_facts
from invest_ml.defs.assets.market import price_bars
from invest_ml.defs.assets.modeling import (
    current_predictions,
    matured_labels,
    trained_model,
    training_dataset,
)
from invest_ml.defs.jobs import all_jobs
from invest_ml.defs.resources import ArtifactStoreResource, PostgresResource, SecBulkResource
from invest_ml.defs.schedules import all_schedules

_all_assets = [
    # Discovery group
    company_catalog,
    companyfacts_data_profiles,
    candidate_universe,
    company_market_profiles,
    training_universe,
    scoring_universe,
    # Financial warehouse group
    selected_companyfacts_raw,
    xbrl_facts,
    canonical_metrics,
    price_bars,
    # ML group
    feature_registry,
    feature_snapshots,
    matured_labels,
    training_dataset,
    trained_model,
    current_predictions,
]

defs = Definitions(
    assets=_all_assets,
    jobs=all_jobs,
    schedules=all_schedules,
    resources={
        "postgres": PostgresResource(database_url=EnvVar("DATABASE_URL")),
        "sec_bulk": SecBulkResource(user_agent=EnvVar("SEC_USER_AGENT")),
        "artifact_store": ArtifactStoreResource(),
    },
)
