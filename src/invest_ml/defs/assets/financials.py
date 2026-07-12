"""Financial warehouse Dagster assets.

Asset graph: training_universe → xbrl_facts → canonical_metrics
"""

from datetime import UTC, datetime

from dagster import AssetExecutionContext, asset

from invest_ml.defs.resources import PostgresResource, SecBulkResource


@asset(
    group_name="financial_warehouse",
    deps=["training_universe"],
    description=(
        "Flattened XBRL facts for all training-universe members, persisted to xbrl_facts. "
        "Downloads (or reuses) the SEC bulk companyfacts ZIP, hashes each member, "
        "registers lineage in raw_source_versions/raw_version_derivations, "
        "and bulk-inserts facts whose (taxonomy, tag) pair appears in the "
        "canonical metrics registry. Ingestion is idempotent via deterministic fact_id."
    ),
)
def xbrl_facts(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    sec_bulk: SecBulkResource,
) -> None:
    from invest_ml.config.loaders import load_canonical_metrics, load_universe_config
    from invest_ml.db.repositories.universe import UniverseRepository
    from invest_ml.sec.companyfacts_flattener import CompanyFactsFlattener
    from invest_ml.xbrl.service import XbrlFactsIngestionService

    universe_cfg = load_universe_config()
    training_cfg = universe_cfg["training"]
    universe_name: str = training_cfg["name"]
    universe_version: str = training_cfg["version"]

    metrics_cfg = load_canonical_metrics()
    flattener = CompanyFactsFlattener.from_config(metrics_cfg)

    archive_cache = sec_bulk.make_archive_cache()
    session_factory = postgres.get_session_factory()

    service = XbrlFactsIngestionService(
        session_factory=session_factory,
        archive_cache=archive_cache,
        flattener=flattener,
        max_member_bytes=sec_bulk.max_zip_member_bytes,
        force_refresh=sec_bulk.force_refresh,
        cache_only=sec_bulk.cache_only,
    )

    ingested_at = datetime.now(tz=UTC)

    with session_factory() as session:
        repo = UniverseRepository(session)
        run = repo.create_ingestion_run(
            source="sec_companyfacts",
            source_uri=sec_bulk.companyfacts_bulk_url,
            started_at=ingested_at,
        )
        session.commit()
        run_id = run.run_id

    try:
        result = service.materialize(
            universe_name=universe_name,
            universe_version=universe_version,
            source_run_id=run_id,
            ingested_at=ingested_at,
        )
    except Exception as exc:
        with session_factory() as session:
            repo = UniverseRepository(session)
            repo.fail_ingestion_run(run_id, error=str(exc))
            session.commit()
        raise

    with session_factory() as session:
        repo = UniverseRepository(session)
        repo.succeed_ingestion_run(
            run_id,
            entities_checked=result.members_processed + result.members_skipped_not_found,
            entities_changed=result.members_succeeded,
            extra_metadata={
                "members_processed": result.members_processed,
                "members_succeeded": result.members_succeeded,
                "members_failed": result.members_failed,
                "members_skipped_not_found": result.members_skipped_not_found,
                "members_skipped_already_done": result.members_skipped_already_done,
                "total_facts_inserted": result.total_facts_inserted,
                "derivation_version": result.derivation_version,
            },
        )
        session.commit()

    context.add_output_metadata({
        "members_processed": result.members_processed,
        "members_succeeded": result.members_succeeded,
        "members_failed": result.members_failed,
        "members_skipped_not_found": result.members_skipped_not_found,
        "total_facts_inserted": result.total_facts_inserted,
        "derivation_version": result.derivation_version,
    })


@asset(
    group_name="financial_warehouse",
    deps=["xbrl_facts"],
    description=(
        "Point-in-time normalized financial metrics derived from xbrl_facts. "
        "available_at = filed_date (point-in-time invariant). "
        "Revisions are preserved; old rows are never deleted. "
        "Idempotent: same normalization_version + same facts → same rows."
    ),
)
def canonical_metrics(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    from datetime import UTC, datetime

    from invest_ml.canonical.registry import CanonicalMetricRegistry
    from invest_ml.canonical.service import CanonicalMetricNormalizationService
    from invest_ml.config.loaders import load_canonical_metrics
    from invest_ml.db.repositories.universe import UniverseRepository

    _NORMALIZATION_VERSION = "canonical_metrics_v1"

    metrics_cfg = load_canonical_metrics()
    registry = CanonicalMetricRegistry.from_config(metrics_cfg)

    session_factory = postgres.get_session_factory()
    ingested_at = datetime.now(tz=UTC)

    with session_factory() as session:
        run_repo = UniverseRepository(session)
        run = run_repo.create_ingestion_run(
            source="canonical_metrics",
            source_uri=f"xbrl_facts/{registry.configuration_hash[:16]}",
            started_at=ingested_at,
        )
        session.commit()
        run_id = run.run_id

    service = CanonicalMetricNormalizationService(session_factory=session_factory)

    try:
        result = service.materialize(
            normalization_version=_NORMALIZATION_VERSION,
            configuration=registry,
            company_ids=None,
            ingested_at=ingested_at,
        )
    except Exception as exc:
        with session_factory() as session:
            run_repo = UniverseRepository(session)
            run_repo.fail_ingestion_run(run_id, error=str(exc)[:2000])
            session.commit()
        raise

    with session_factory() as session:
        run_repo = UniverseRepository(session)
        run_repo.succeed_ingestion_run(
            run_id,
            entities_checked=result.companies_considered,
            entities_changed=result.metrics_created,
            extra_metadata={
                "normalization_version": result.normalization_version,
                "configuration_hash": result.configuration_hash,
                "companies_considered": result.companies_considered,
                "companies_with_metrics": result.companies_with_metrics,
                "metrics_resolved": result.metrics_resolved,
                "metrics_created": result.metrics_created,
                "metrics_already_present": result.metrics_already_present,
                "annual_metrics_resolved": result.annual_metrics_resolved,
                "quarterly_metrics_resolved": result.quarterly_metrics_resolved,
                "facts_considered": result.facts_considered,
                "facts_rejected": result.facts_rejected,
            },
        )
        session.commit()

    context.add_output_metadata({
        "normalization_version": result.normalization_version,
        "configuration_hash": result.configuration_hash,
        "companies_considered": result.companies_considered,
        "companies_with_metrics": result.companies_with_metrics,
        "metrics_resolved": result.metrics_resolved,
        "metrics_created": result.metrics_created,
        "metrics_already_present": result.metrics_already_present,
        "annual_metrics_resolved": result.annual_metrics_resolved,
        "quarterly_metrics_resolved": result.quarterly_metrics_resolved,
        "candidate_groups": result.candidate_groups,
        "facts_considered": result.facts_considered,
        "facts_rejected": result.facts_rejected,
    })
