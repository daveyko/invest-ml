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
        "Point-in-time normalized financial metrics. "
        "available_at <= as_of_date constraint is enforced during feature construction. "
        "Revisions are preserved; old rows are never deleted."
    ),
)
def canonical_metrics(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    raise NotImplementedError(
        "TODO: call sec.normalizer.normalize_facts, bulk_insert_ignore into canonical_metrics"
    )
