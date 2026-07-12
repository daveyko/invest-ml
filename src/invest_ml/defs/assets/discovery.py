"""Discovery-group Dagster assets."""

import logging
import time
from datetime import UTC, datetime

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from invest_ml.defs.resources import (
    ArtifactStoreResource,
    EquityMarketDataResource,
    PostgresResource,
    SecBulkResource,
)

logger = logging.getLogger(__name__)

_SEC_SUBMISSIONS_SOURCE = "sec_submissions_bulk"
_SEC_COMPANYFACTS_SOURCE = "sec_companyfacts_bulk_profile"
_PROFILE_BATCH_SIZE = 500


@asset(
    group_name="discovery",
    description=(
        "Lightweight SEC metadata for all companies observed in the submissions feed. "
        "Stored for ALL CIKs — not only universe members. "
        "Does not store filing history, CompanyFacts, market prices, or universe decisions."
    ),
)
def company_catalog(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    sec_bulk: SecBulkResource,
    artifact_store: ArtifactStoreResource,
) -> MaterializeResult:
    """Refresh the company catalog from the SEC bulk submissions archive.

    Flow
    ----
    1. Create an IngestionRun (status=running).
    2. Load previous successful run metadata (hash, etag, last-modified).
    3. If local archive exists and SHA-256 matches: mark succeeded, skip download.
    4. Otherwise download via SecClient (conditional request when etag/LM available).
    5. If HTTP 304 or downloaded SHA-256 matches previous: mark succeeded, skip parse.
    6. Otherwise: parse + upsert via CompanyCatalogService.
    7. Mark run succeeded with archive metadata.
    8. On any failure: mark run failed, re-raise.
    9. Delete the archive unless retain_archives=True.
    """
    from invest_ml.company_catalog.service import CompanyCatalogService
    from invest_ml.db.repositories.company_catalog import CompanyCatalogRepository
    from invest_ml.sec.archive import SubmissionArchiveReader

    session_factory = postgres.get_session_factory()
    t_start = time.monotonic()

    # ── 1. Create ingestion run ───────────────────────────────────────────────
    with session_factory() as session:
        repo = CompanyCatalogRepository(session)
        run = repo.create_ingestion_run(
            source=_SEC_SUBMISSIONS_SOURCE,
            source_uri=sec_bulk.submissions_bulk_url,
            started_at=datetime.now(tz=UTC),
        )
        session.commit()
        run_id = run.run_id

    context.log.info("Created IngestionRun %s", run_id)

    try:
        # ── 2. Previous run metadata for conditional request ──────────────────
        with session_factory() as session:
            repo = CompanyCatalogRepository(session)
            prev = repo.find_latest_successful_ingestion_run(_SEC_SUBMISSIONS_SOURCE)
            previous_etag = prev.etag if prev else None
            previous_last_modified = prev.last_modified if prev else None
            previous_hash = prev.archive_hash if prev else None

        # ── 3. Check local cache before hitting the network ───────────────────
        cached_path = sec_bulk.find_cached_archive(previous_hash)
        if cached_path is not None:
            context.log.info(
                "Local archive matches previous hash (%.16s); skipping download.",
                previous_hash,
            )
            _succeed_unchanged(
                session_factory, CompanyCatalogRepository, run_id,
                archive_hash=previous_hash,
                etag=previous_etag,
                last_modified=previous_last_modified,
                reason="local cache hit",
            )
            duration = time.monotonic() - t_start
            return MaterializeResult(
                metadata={
                    "changed": MetadataValue.bool(False),
                    "skip_reason": MetadataValue.text("local cache hit"),
                    "duration_seconds": MetadataValue.float(round(duration, 1)),
                }
            )

        # ── 4. Download ───────────────────────────────────────────────────────
        client = sec_bulk.make_client()
        context.log.info("Downloading submissions archive from %s", sec_bulk.submissions_bulk_url)
        result = client.download_submissions_archive(
            sec_bulk.download_dir_path,
            previous_etag=previous_etag,
            previous_last_modified=previous_last_modified,
        )

        # ── 5. Skip if unchanged (304 or same hash after download) ────────────
        if result.not_modified or (
            previous_hash and result.sha256 and result.sha256 == previous_hash
        ):
            reason = "HTTP 304" if result.not_modified else "identical SHA-256"
            context.log.info("Archive unchanged (%s); skipping parse.", reason)
            _succeed_unchanged(
                session_factory, CompanyCatalogRepository, run_id,
                archive_hash=result.sha256 or previous_hash,
                etag=result.etag or previous_etag,
                last_modified=result.last_modified or previous_last_modified,
                reason=reason,
            )

            duration = time.monotonic() - t_start
            return MaterializeResult(
                metadata={
                    "changed": MetadataValue.bool(False),
                    "skip_reason": MetadataValue.text(reason),
                    "duration_seconds": MetadataValue.float(round(duration, 1)),
                }
            )

        # ── 5. Parse and persist ──────────────────────────────────────────────
        observed_at = result.downloaded_at
        context.log.info(
            "Processing archive: sha256=%.16s bytes=%d",
            result.sha256, result.byte_size,
        )

        service = CompanyCatalogService(
            session_factory=session_factory,
            archive_reader=SubmissionArchiveReader(
                max_member_bytes=sec_bulk.max_zip_member_bytes
            ),
        )
        catalog_result = service.refresh_catalog(result.path, run_id, observed_at)

        entities_changed = (
            catalog_result.companies_inserted
            + catalog_result.securities_inserted
            + catalog_result.sic_classifications_inserted
        )

        # ── 6. Mark success ───────────────────────────────────────────────────
        with session_factory() as session:
            repo = CompanyCatalogRepository(session)
            repo.succeed_ingestion_run(
                run_id,
                archive_hash=result.sha256,
                etag=result.etag,
                last_modified=result.last_modified,
                entities_checked=catalog_result.companies_seen,
                entities_changed=entities_changed,
                extra_metadata={
                    "parse_warnings_count": len(catalog_result.parse_warnings),
                    "malformed_records": catalog_result.malformed_records,
                },
            )
            session.commit()

        # ── 7. Optionally delete archive ──────────────────────────────────────
        if not sec_bulk.retain_archives and result.path.exists():
            result.path.unlink()
            context.log.info("Deleted archive %s", result.path)

        duration = time.monotonic() - t_start
        context.log.info(
            "company_catalog complete: companies_seen=%d inserted=%d updated=%d "
            "securities_inserted=%d sic_inserted=%d malformed=%d warnings=%d "
            "duration=%.1fs",
            catalog_result.companies_seen,
            catalog_result.companies_inserted,
            catalog_result.companies_updated,
            catalog_result.securities_inserted,
            catalog_result.sic_classifications_inserted,
            catalog_result.malformed_records,
            len(catalog_result.parse_warnings),
            duration,
        )

        # ── 8. Emit metadata ──────────────────────────────────────────────────
        return MaterializeResult(
            metadata={
                "archive_sha256": MetadataValue.text(result.sha256),
                "archive_bytes": MetadataValue.int(result.byte_size),
                "companies_seen": MetadataValue.int(catalog_result.companies_seen),
                "companies_inserted": MetadataValue.int(catalog_result.companies_inserted),
                "companies_updated": MetadataValue.int(catalog_result.companies_updated),
                "securities_inserted": MetadataValue.int(catalog_result.securities_inserted),
                "securities_updated": MetadataValue.int(catalog_result.securities_updated),
                "sic_classifications_inserted": MetadataValue.int(
                    catalog_result.sic_classifications_inserted
                ),
                "malformed_records": MetadataValue.int(catalog_result.malformed_records),
                "parse_warnings": MetadataValue.int(len(catalog_result.parse_warnings)),
                "changed": MetadataValue.bool(True),
                "duration_seconds": MetadataValue.float(round(duration, 1)),
            }
        )

    except Exception as exc:
        # ── Failure: mark run and re-raise ────────────────────────────────────
        error_text = f"{type(exc).__name__}: {exc}"
        context.log.error("company_catalog failed: %s", error_text)
        try:
            with session_factory() as session:
                repo = CompanyCatalogRepository(session)
                repo.fail_ingestion_run(run_id, error=error_text)
                session.commit()
        except Exception:
            context.log.exception("Could not mark IngestionRun %s as failed", run_id)
        raise


# ── Private helpers ──────────────────────────────────────────────────────────


def _succeed_unchanged(
    session_factory,
    repo_cls,
    run_id,
    *,
    archive_hash,
    etag,
    last_modified,
    reason: str,
) -> None:
    """Mark an ingestion run succeeded without any data change."""
    with session_factory() as session:
        repo = repo_cls(session)
        repo.succeed_ingestion_run(
            run_id,
            archive_hash=archive_hash,
            etag=etag,
            last_modified=last_modified,
            extra_metadata={"skipped_reason": reason},
        )
        session.commit()


# ── Remaining discovery assets (not yet implemented) ────────────────────────


@asset(
    group_name="discovery",
    deps=["company_catalog"],
    description=(
        "Lightweight data-quality profile per company produced by scanning CompanyFacts. "
        "Does NOT persist the raw payload. Used to identify candidate companies."
    ),
)
def companyfacts_data_profiles(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    sec_bulk: SecBulkResource,
    artifact_store: ArtifactStoreResource,
) -> MaterializeResult:
    """Scan the SEC bulk companyfacts archive and upsert one profile per target company.

    Flow
    ----
    1. Load profiling config + universe config to determine target CIKs.
    2. Create an IngestionRun.
    3. Check local cache; skip download if SHA-256 matches previous run.
    4. Download companyfacts.zip (conditional request when etag/LM available).
    5. Skip profiling if 304 or identical SHA-256.
    6. Stream archive → profile each found CIK, create empty profiles for missing.
    7. Batch-upsert all profiles.
    8. Mark run succeeded.
    9. Delete archive unless retain_archives=True.
    """
    from invest_ml.config.loaders import load_canonical_metrics, load_universe_config
    from invest_ml.db.repositories.company_data_profiles import CompanyDataProfileRepository
    from invest_ml.sec.companyfacts_archive import (
        CompanyFactsArchiveReader,
        CompanyFactsArchiveStats,
    )
    from invest_ml.sec.profiler import CompanyFactsProfiler, ProfilingConfig

    # ── Load configs ──────────────────────────────────────────────────────────
    canonical_cfg = load_canonical_metrics()
    universe_cfg = load_universe_config()
    exchanges = universe_cfg.get("candidate", {}).get("exchanges", [])
    entity_types = ["operating"]
    profile_version = sec_bulk.companyfacts_profile_version

    profiling_config = ProfilingConfig.from_canonical_metrics(canonical_cfg)
    profiler = CompanyFactsProfiler(profiling_config)

    session_factory = postgres.get_session_factory()

    # ── 1. Load targets ───────────────────────────────────────────────────────
    with session_factory() as session:
        repo = CompanyDataProfileRepository(session)
        targets = repo.list_companyfacts_profile_targets(
            exchanges=exchanges,
            entity_types=entity_types,
        )

    target_ciks: set = {t.cik for t in targets}
    cik_to_company_id: dict = {t.cik: t.company_id for t in targets}
    context.log.info("Loaded %d companyfacts profile targets", len(targets))

    t_start = time.monotonic()

    # ── 2. Create ingestion run ───────────────────────────────────────────────
    with session_factory() as session:
        repo = CompanyDataProfileRepository(session)
        run = repo.create_ingestion_run(
            source=_SEC_COMPANYFACTS_SOURCE,
            source_uri=sec_bulk.companyfacts_bulk_url,
            started_at=datetime.now(tz=UTC),
        )
        session.commit()
        run_id = run.run_id

    context.log.info("Created IngestionRun %s", run_id)

    try:
        # ── 3. Previous run metadata ──────────────────────────────────────────
        with session_factory() as session:
            repo = CompanyDataProfileRepository(session)
            prev = repo.find_latest_successful_ingestion_run(_SEC_COMPANYFACTS_SOURCE)
            previous_etag = prev.etag if prev else None
            previous_last_modified = prev.last_modified if prev else None
            previous_hash = prev.archive_hash if prev else None

        # ── 4. Check local cache ──────────────────────────────────────────────
        cached_path = sec_bulk.find_cached_archive(
            previous_hash, filename="companyfacts.zip"
        )
        if cached_path is not None:
            context.log.info(
                "Local companyfacts archive matches previous hash (%.16s); skipping download.",
                previous_hash,
            )
            _succeed_unchanged(
                session_factory, CompanyDataProfileRepository, run_id,
                archive_hash=previous_hash,
                etag=previous_etag,
                last_modified=previous_last_modified,
                reason="local cache hit",
            )
            duration = time.monotonic() - t_start
            return MaterializeResult(
                metadata={
                    "changed": MetadataValue.bool(False),
                    "skip_reason": MetadataValue.text("local cache hit"),
                    "duration_seconds": MetadataValue.float(round(duration, 1)),
                }
            )

        # ── 5. Download ───────────────────────────────────────────────────────
        client = sec_bulk.make_client()
        context.log.info(
            "Downloading companyfacts archive from %s", sec_bulk.companyfacts_bulk_url
        )
        result = client.download_archive(
            sec_bulk.companyfacts_bulk_url,
            sec_bulk.download_dir_path,
            "companyfacts.zip",
            previous_etag=previous_etag,
            previous_last_modified=previous_last_modified,
        )

        # ── 6. Skip if unchanged ──────────────────────────────────────────────
        if result.not_modified or (
            previous_hash and result.sha256 and result.sha256 == previous_hash
        ):
            reason = "HTTP 304" if result.not_modified else "identical SHA-256"
            context.log.info(
                "Companyfacts archive unchanged (%s); skipping profiling.", reason
            )
            _succeed_unchanged(
                session_factory, CompanyDataProfileRepository, run_id,
                archive_hash=result.sha256 or previous_hash,
                etag=result.etag or previous_etag,
                last_modified=result.last_modified or previous_last_modified,
                reason=reason,
            )
            duration = time.monotonic() - t_start
            return MaterializeResult(
                metadata={
                    "changed": MetadataValue.bool(False),
                    "skip_reason": MetadataValue.text(reason),
                    "duration_seconds": MetadataValue.float(round(duration, 1)),
                }
            )

        # ── 7. Profile companies ──────────────────────────────────────────────
        scanned_at = datetime.now(tz=UTC)
        reader = CompanyFactsArchiveReader(
            max_member_bytes=sec_bulk.max_zip_member_bytes
        )
        stats = CompanyFactsArchiveStats()
        profiles = []

        context.log.info(
            "Processing companyfacts archive: sha256=%.16s bytes=%d targets=%d",
            result.sha256, result.byte_size, len(target_ciks),
        )

        for record in reader.iter_target_records(result.path, target_ciks, stats=stats):
            company_id = cik_to_company_id[record.cik]
            profiles.append(
                profiler.profile(
                    company_id=company_id,
                    cik=record.cik,
                    payload=record.payload,
                    profile_version=profile_version,
                    scanned_at=scanned_at,
                    source_run_id=run_id,
                    cik_mismatch=record.cik_mismatch,
                )
            )

        # ── 8. Empty profiles for CIKs absent from the archive ───────────────
        missing_ciks = target_ciks - stats.found_ciks
        for cik in missing_ciks:
            profiles.append(
                profiler.profile_missing(
                    company_id=cik_to_company_id[cik],
                    cik=cik,
                    profile_version=profile_version,
                    scanned_at=scanned_at,
                    source_run_id=run_id,
                )
            )

        context.log.info(
            "Profiling complete: found=%d missing=%d cik_mismatches=%d",
            len(stats.found_ciks), len(missing_ciks), stats.cik_mismatches,
        )

        # ── 9. Batch-upsert profiles ──────────────────────────────────────────
        total_upserted = 0
        for i in range(0, len(profiles), _PROFILE_BATCH_SIZE):
            batch = profiles[i : i + _PROFILE_BATCH_SIZE]
            with session_factory() as session:
                repo = CompanyDataProfileRepository(session)
                upsert_result = repo.upsert_profiles(batch)
                session.commit()
            total_upserted += upsert_result.upserted

        # ── 10. Mark success ──────────────────────────────────────────────────
        with session_factory() as session:
            repo = CompanyDataProfileRepository(session)
            repo.succeed_ingestion_run(
                run_id,
                archive_hash=result.sha256,
                etag=result.etag,
                last_modified=result.last_modified,
                entities_checked=len(target_ciks),
                entities_changed=total_upserted,
                extra_metadata={
                    "missing_in_archive": len(missing_ciks),
                    "cik_mismatches": stats.cik_mismatches,
                    "duplicates_skipped": len(stats.duplicate_ciks),
                },
            )
            session.commit()

        # ── 11. Optionally delete archive ─────────────────────────────────────
        if not sec_bulk.retain_archives and result.path.exists():
            result.path.unlink()
            context.log.info("Deleted companyfacts archive %s", result.path)

        duration = time.monotonic() - t_start
        context.log.info(
            "companyfacts_data_profiles complete: targets=%d found=%d missing=%d "
            "upserted=%d duration=%.1fs",
            len(target_ciks), len(stats.found_ciks), len(missing_ciks),
            total_upserted, duration,
        )

        return MaterializeResult(
            metadata={
                "archive_sha256": MetadataValue.text(result.sha256),
                "archive_bytes": MetadataValue.int(result.byte_size),
                "targets": MetadataValue.int(len(target_ciks)),
                "found_in_archive": MetadataValue.int(len(stats.found_ciks)),
                "missing_in_archive": MetadataValue.int(len(missing_ciks)),
                "profiles_upserted": MetadataValue.int(total_upserted),
                "cik_mismatches": MetadataValue.int(stats.cik_mismatches),
                "changed": MetadataValue.bool(True),
                "duration_seconds": MetadataValue.float(round(duration, 1)),
            }
        )

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        context.log.error("companyfacts_data_profiles failed: %s", error_text)
        try:
            with session_factory() as session:
                repo = CompanyDataProfileRepository(session)
                repo.fail_ingestion_run(run_id, error=error_text)
                session.commit()
        except Exception:
            context.log.exception("Could not mark IngestionRun %s as failed", run_id)
        raise


_UNIVERSE_SOURCE = "candidate_universe"
_UNIVERSE_NAME = "candidate"
_UNIVERSE_VERSION = "v1"
_PROFILE_VERSION = "companyfacts_profile_v1"


@asset(
    group_name="discovery",
    deps=["companyfacts_data_profiles"],
    description="US-listed operating companies with a recent SEC filing.",
)
def candidate_universe(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> MaterializeResult:
    """Evaluate all companies and persist effective-dated universe memberships.

    Flow
    ----
    1. Load universe config; parse CandidateUniverseConfig.
    2. Create an IngestionRun.
    3. Call CandidateUniverseService.materialize() — creates/validates the
       UniverseDefinition, evaluates all companies, diffs active memberships,
       and persists changes atomically.
    4. Mark run succeeded with aggregate statistics.
    5. On any failure: mark run failed, re-raise.
    """
    from datetime import UTC, date, datetime

    from invest_ml.config.loaders import load_universe_config
    from invest_ml.db.repositories.universe import UniverseRepository
    from invest_ml.universe.config import CandidateUniverseConfig
    from invest_ml.universe.service import CandidateUniverseService

    session_factory = postgres.get_session_factory()
    as_of_date = date.today()
    t_start = time.monotonic()

    universe_cfg = load_universe_config()
    config = CandidateUniverseConfig.from_dict(
        universe_cfg.get("candidate", {}),
        profile_version=_PROFILE_VERSION,
    )

    # ── 1. Create ingestion run ───────────────────────────────────────────────
    with session_factory() as session:
        repo = UniverseRepository(session)
        run = repo.create_ingestion_run(
            source=_UNIVERSE_SOURCE,
            source_uri=f"{_UNIVERSE_NAME}:{_UNIVERSE_VERSION}",
            started_at=datetime.now(tz=UTC),
        )
        session.commit()
        run_id = run.run_id

    context.log.info("Created IngestionRun %s for candidate_universe", run_id)

    try:
        # ── 2. Materialize universe ───────────────────────────────────────────
        service = CandidateUniverseService(session_factory=session_factory)
        result = service.materialize(
            as_of_date=as_of_date,
            universe_name=_UNIVERSE_NAME,
            universe_version=_UNIVERSE_VERSION,
            profile_version=config.profile_version,
            config=config,
        )

        # ── 3. Mark success ───────────────────────────────────────────────────
        with session_factory() as session:
            repo = UniverseRepository(session)
            repo.succeed_ingestion_run(
                run_id,
                entities_checked=result.evaluated_companies,
                entities_changed=result.newly_included + result.newly_excluded,
                extra_metadata={
                    "included": result.included_companies,
                    "newly_included": result.newly_included,
                    "already_included": result.already_included,
                    "newly_excluded": result.newly_excluded,
                    "exclusion_counts": result.exclusion_counts,
                    "criteria_hash": result.criteria_hash,
                    "as_of_date": as_of_date.isoformat(),
                },
            )
            session.commit()

        duration = time.monotonic() - t_start
        context.log.info(
            "candidate_universe complete: evaluated=%d included=%d "
            "newly_included=%d newly_excluded=%d duration=%.1fs",
            result.evaluated_companies,
            result.included_companies,
            result.newly_included,
            result.newly_excluded,
            duration,
        )

        return MaterializeResult(
            metadata={
                "universe_id": MetadataValue.text(str(result.universe_id)),
                "criteria_hash": MetadataValue.text(result.criteria_hash[:16] + "..."),
                "evaluated_companies": MetadataValue.int(result.evaluated_companies),
                "included_companies": MetadataValue.int(result.included_companies),
                "newly_included": MetadataValue.int(result.newly_included),
                "already_included": MetadataValue.int(result.already_included),
                "newly_excluded": MetadataValue.int(result.newly_excluded),
                "as_of_date": MetadataValue.text(as_of_date.isoformat()),
                "duration_seconds": MetadataValue.float(round(duration, 1)),
            }
        )

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        context.log.error("candidate_universe failed: %s", error_text)
        try:
            with session_factory() as session:
                repo = UniverseRepository(session)
                repo.fail_ingestion_run(run_id, error=error_text)
                session.commit()
        except Exception:
            context.log.exception("Could not mark IngestionRun %s as failed", run_id)
        raise


_MARKET_DATA_SOURCE = "company_market_profiles"
_MARKET_PROFILE_VERSION = "market_profile_v1"
_MARKET_UNIVERSE_NAME = "candidate"
_MARKET_UNIVERSE_VERSION = "v1"


@asset(
    group_name="discovery",
    deps=["candidate_universe"],
    description="Price and investability profile for each candidate security.",
)
def company_market_profiles(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    equity_market_data: EquityMarketDataResource,
) -> MaterializeResult:
    """Fetch EOD bars and compute a market profile for each candidate security.

    Flow
    ----
    1. Load market data config.
    2. Build price provider and optional market-cap provider.
    3. Call CompanyMarketProfileService.materialize().
    4. Return MaterializeResult with aggregate statistics.
    """
    from datetime import date

    from invest_ml.config.loaders import load_market_data_config
    from invest_ml.market.service import CompanyMarketProfileService, MarketProfileRunConfig

    session_factory = postgres.get_session_factory()
    as_of_date = date.today()
    t_start = time.monotonic()

    market_cfg = load_market_data_config()
    profiles_cfg = market_cfg.get("market_profiles", {})
    symbol_overrides = market_cfg.get("market_data", {}).get("symbol_overrides", {})

    run_config = MarketProfileRunConfig(
        universe_name=profiles_cfg.get("universe_name", _MARKET_UNIVERSE_NAME),
        universe_version=profiles_cfg.get("universe_version", _MARKET_UNIVERSE_VERSION),
        profile_version=profiles_cfg.get("profile_version", _MARKET_PROFILE_VERSION),
        history_lookback_years=profiles_cfg.get("history_lookback_years", 3),
        refresh_after_days=profiles_cfg.get("refresh_after_days", 30),
        failed_symbol_retry_after_days=profiles_cfg.get("failed_symbol_retry_after_days", 30),
        liquidity_lookback_sessions=profiles_cfg.get("liquidity_lookback_sessions", 90),
        missing_ratio_lookback_years=profiles_cfg.get("missing_ratio_lookback_years", 3),
        maximum_symbols_per_run=profiles_cfg.get(
            "maximum_symbols_per_run", equity_market_data.maximum_symbols_per_run
        ),
    )

    price_provider = equity_market_data.build_price_provider(symbol_overrides)
    market_cap_provider = equity_market_data.build_market_cap_provider(symbol_overrides)

    service = CompanyMarketProfileService(
        session_factory=session_factory,
        price_provider=price_provider,
        market_cap_provider=market_cap_provider,
    )

    result = service.materialize(as_of_date=as_of_date, config=run_config)

    duration = time.monotonic() - t_start
    context.log.info(
        "company_market_profiles complete: targets=%d succeeded=%d "
        "not_found=%d temp_failure=%d duration=%.1fs",
        result.targets_found,
        result.profiles_succeeded,
        result.profiles_not_found,
        result.profiles_temporary_failure,
        duration,
    )

    return MaterializeResult(
        metadata={
            "targets_found": MetadataValue.int(result.targets_found),
            "profiles_succeeded": MetadataValue.int(result.profiles_succeeded),
            "profiles_not_found": MetadataValue.int(result.profiles_not_found),
            "profiles_temporary_failure": MetadataValue.int(result.profiles_temporary_failure),
            "market_cap_disabled": MetadataValue.bool(result.market_cap_disabled),
            "metadata_requests": MetadataValue.int(result.metadata_requests),
            "price_requests": MetadataValue.int(result.price_requests),
            "market_cap_requests": MetadataValue.int(result.market_cap_requests),
            "as_of_date": MetadataValue.text(as_of_date.isoformat()),
            "duration_seconds": MetadataValue.float(round(duration, 1)),
        }
    )


_TRAINING_UNIVERSE_SOURCE = "training_universe"
_SCORING_UNIVERSE_SOURCE = "scoring_universe"


@asset(
    group_name="discovery",
    deps=["company_market_profiles"],
    description=(
        "Broad universe of financially eligible companies with a selected representative "
        "security. Data-quality driven only — no thematic filters."
    ),
)
def training_universe(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> MaterializeResult:
    """Evaluate all candidate-universe members and persist training-universe memberships.

    Flow
    ----
    1. Parse TrainingUniverseConfig from universe_v1.yaml.
    2. Create an IngestionRun.
    3. Call TrainingUniverseService.materialize() — creates/validates the
       UniverseDefinition, runs deterministic security selection, diffs active
       memberships, and persists changes atomically.
    4. Mark run succeeded with aggregate statistics.
    5. On any failure: mark run failed, re-raise.
    """
    from datetime import date

    from invest_ml.config.loaders import load_universe_config
    from invest_ml.db.repositories.universe import UniverseRepository
    from invest_ml.universe.service import TrainingUniverseService
    from invest_ml.universe.training import TrainingUniverseConfig

    session_factory = postgres.get_session_factory()
    as_of_date = date.today()
    t_start = time.monotonic()

    universe_cfg = load_universe_config()
    config = TrainingUniverseConfig.from_dict(universe_cfg.get("training", {}))

    with session_factory() as session:
        repo = UniverseRepository(session)
        run = repo.create_ingestion_run(
            source=_TRAINING_UNIVERSE_SOURCE,
            source_uri=f"{config.name}:{config.version}",
            started_at=datetime.now(tz=UTC),
        )
        session.commit()
        run_id = run.run_id

    context.log.info("Created IngestionRun %s for training_universe", run_id)

    try:
        service = TrainingUniverseService(session_factory=session_factory)
        result = service.materialize(as_of_date=as_of_date, config=config)

        with session_factory() as session:
            repo = UniverseRepository(session)
            repo.succeed_ingestion_run(
                run_id,
                entities_checked=result.evaluated_companies,
                entities_changed=(
                    result.newly_included
                    + result.newly_excluded
                    + result.selected_security_changes
                ),
                extra_metadata={
                    "included": result.included_companies,
                    "newly_included": result.newly_included,
                    "already_included": result.already_included,
                    "newly_excluded": result.newly_excluded,
                    "selected_security_changes": result.selected_security_changes,
                    "exclusion_counts": result.exclusion_counts,
                    "criteria_hash": result.criteria_hash,
                    "as_of_date": as_of_date.isoformat(),
                },
            )
            session.commit()

        duration = time.monotonic() - t_start
        context.log.info(
            "training_universe complete: evaluated=%d included=%d "
            "newly_included=%d security_changes=%d newly_excluded=%d duration=%.1fs",
            result.evaluated_companies,
            result.included_companies,
            result.newly_included,
            result.selected_security_changes,
            result.newly_excluded,
            duration,
        )

        return MaterializeResult(
            metadata={
                "universe_id": MetadataValue.text(str(result.universe_id)),
                "criteria_hash": MetadataValue.text(result.criteria_hash[:16] + "..."),
                "evaluated_companies": MetadataValue.int(result.evaluated_companies),
                "included_companies": MetadataValue.int(result.included_companies),
                "newly_included": MetadataValue.int(result.newly_included),
                "already_included": MetadataValue.int(result.already_included),
                "newly_excluded": MetadataValue.int(result.newly_excluded),
                "selected_security_changes": MetadataValue.int(result.selected_security_changes),
                "as_of_date": MetadataValue.text(as_of_date.isoformat()),
                "duration_seconds": MetadataValue.float(round(duration, 1)),
            }
        )

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        context.log.error("training_universe failed: %s", error_text)
        try:
            with session_factory() as session:
                repo = UniverseRepository(session)
                repo.fail_ingestion_run(run_id, error=error_text)
                session.commit()
        except Exception:
            context.log.exception("Could not mark IngestionRun %s as failed", run_id)
        raise


@asset(
    group_name="discovery",
    deps=["training_universe"],
    description=(
        "Narrower scoring universe: model-bucket SIC matching (semiconductors, "
        "software, fintech, etc.) plus manual inclusions."
    ),
)
def scoring_universe(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> MaterializeResult:
    """Filter training-universe members to the scoring universe via SIC buckets.

    Flow
    ----
    1. Parse ScoringUniverseConfig and SicBucketConfig from YAML.
    2. Create an IngestionRun.
    3. Validate manual ticker configuration (fail early if any ticker is ambiguous).
    4. Call ScoringUniverseService.materialize() — creates/validates the
       UniverseDefinition, evaluates all training members, and persists changes.
    5. Mark run succeeded with aggregate statistics.
    6. On any failure: mark run failed, re-raise.
    """
    from datetime import date

    from invest_ml.config.loaders import load_sic_buckets, load_universe_config
    from invest_ml.db.repositories.universe import UniverseRepository
    from invest_ml.universe.scoring import ScoringUniverseConfig, SicBucketConfig
    from invest_ml.universe.service import ScoringUniverseService

    session_factory = postgres.get_session_factory()
    as_of_date = date.today()
    t_start = time.monotonic()

    universe_cfg = load_universe_config()
    sic_cfg = load_sic_buckets()

    config = ScoringUniverseConfig.from_dict(universe_cfg.get("scoring", {}))
    sic_buckets = SicBucketConfig.from_dict(sic_cfg)

    with session_factory() as session:
        repo = UniverseRepository(session)
        run = repo.create_ingestion_run(
            source=_SCORING_UNIVERSE_SOURCE,
            source_uri=f"{config.name}:{config.version}",
            started_at=datetime.now(tz=UTC),
        )
        session.commit()
        run_id = run.run_id

    context.log.info("Created IngestionRun %s for scoring_universe", run_id)

    try:
        service = ScoringUniverseService(session_factory=session_factory)
        result = service.materialize(
            as_of_date=as_of_date,
            config=config,
            sic_buckets=sic_buckets,
        )

        with session_factory() as session:
            repo = UniverseRepository(session)
            repo.succeed_ingestion_run(
                run_id,
                entities_checked=result.evaluated_training_members,
                entities_changed=result.newly_included + result.newly_excluded,
                extra_metadata={
                    "included": result.included_companies,
                    "bucket_inclusions": result.bucket_inclusions,
                    "manual_inclusions": result.manual_inclusions,
                    "newly_included": result.newly_included,
                    "already_included": result.already_included,
                    "newly_excluded": result.newly_excluded,
                    "bucket_counts": result.bucket_counts,
                    "exclusion_counts": result.exclusion_counts,
                    "criteria_hash": result.criteria_hash,
                    "as_of_date": as_of_date.isoformat(),
                },
            )
            session.commit()

        duration = time.monotonic() - t_start
        context.log.info(
            "scoring_universe complete: evaluated=%d included=%d "
            "bucket=%d manual=%d newly_included=%d newly_excluded=%d duration=%.1fs",
            result.evaluated_training_members,
            result.included_companies,
            result.bucket_inclusions,
            result.manual_inclusions,
            result.newly_included,
            result.newly_excluded,
            duration,
        )

        return MaterializeResult(
            metadata={
                "universe_id": MetadataValue.text(str(result.universe_id)),
                "criteria_hash": MetadataValue.text(result.criteria_hash[:16] + "..."),
                "evaluated_training_members": MetadataValue.int(
                    result.evaluated_training_members
                ),
                "included_companies": MetadataValue.int(result.included_companies),
                "bucket_inclusions": MetadataValue.int(result.bucket_inclusions),
                "manual_inclusions": MetadataValue.int(result.manual_inclusions),
                "newly_included": MetadataValue.int(result.newly_included),
                "already_included": MetadataValue.int(result.already_included),
                "newly_excluded": MetadataValue.int(result.newly_excluded),
                "as_of_date": MetadataValue.text(as_of_date.isoformat()),
                "duration_seconds": MetadataValue.float(round(duration, 1)),
            }
        )

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        context.log.error("scoring_universe failed: %s", error_text)
        try:
            with session_factory() as session:
                repo = UniverseRepository(session)
                repo.fail_ingestion_run(run_id, error=error_text)
                session.commit()
        except Exception:
            context.log.exception("Could not mark IngestionRun %s as failed", run_id)
        raise
