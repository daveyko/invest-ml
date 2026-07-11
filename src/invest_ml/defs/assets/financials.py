"""Financial warehouse Dagster assets.

These assets persist deep financial data for universe members only:
  training_universe → selected_companyfacts_raw → xbrl_facts → canonical_metrics
"""

from dagster import AssetExecutionContext, asset

from invest_ml.defs.resources import ArtifactStoreResource, PostgresResource, SecBulkResource


@asset(
    group_name="financial_warehouse",
    deps=["training_universe"],
    description=(
        "Raw CompanyFacts JSON for each training-universe member, stored in raw_source_versions. "
        "Only universe members receive deep persistence; broad profiling does not."
    ),
)
def selected_companyfacts_raw(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    sec_bulk: SecBulkResource,
    artifact_store: ArtifactStoreResource,
) -> None:
    """Download and persist CompanyFacts for training-universe companies.

    Not yet implemented.
    """
    raise NotImplementedError(
        "TODO: for each training-universe member, fetch CompanyFacts and insert RawSourceVersion"
    )


@asset(
    group_name="financial_warehouse",
    deps=["selected_companyfacts_raw"],
    description=(
        "Flattened XBRL facts derived from raw CompanyFacts. "
        "Ingestion is idempotent via deterministic fact_id hashing."
    ),
)
def xbrl_facts(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    """Parse raw CompanyFacts into XbrlFact rows and bulk-insert.

    Not yet implemented.
    """
    raise NotImplementedError(
        "TODO: call sec.parser.parse_company_facts, bulk_insert_ignore into xbrl_facts"
    )


@asset(
    group_name="financial_warehouse",
    deps=["xbrl_facts"],
    description=(
        "Point-in-time normalized financial metrics. "
        "available_at <= as_of_date constraint is enforced during feature construction. "
        "Revisions are preserved; old rows are never deleted."
    ),
)
def canonical_metrics(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    """Normalize XBRL facts into canonical metrics per the metrics config.

    Not yet implemented.
    """
    raise NotImplementedError(
        "TODO: call sec.normalizer.normalize_facts, bulk_insert_ignore into canonical_metrics"
    )
