"""Discovery-group Dagster assets."""

import logging
import time
from datetime import UTC, datetime

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from invest_ml.defs.resources import ArtifactStoreResource, PostgresResource, SecBulkResource

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


@asset(
    group_name="discovery",
    deps=["companyfacts_data_profiles"],
    description="US-listed operating companies with a recent SEC filing.",
)
def candidate_universe(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    raise NotImplementedError(
        "TODO: call universe.builder.build_candidate_universe, persist to DB"
    )


@asset(
    group_name="discovery",
    deps=["candidate_universe"],
    description="Price and investability profile for each candidate security.",
)
def company_market_profiles(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    artifact_store: ArtifactStoreResource,
) -> None:
    raise NotImplementedError(
        "TODO: fetch price bars per candidate, call market.profiler.profile_security"
    )


@asset(
    group_name="discovery",
    deps=["company_market_profiles"],
    description="Broad universe of companies with sufficient financial history and liquidity.",
)
def training_universe(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    raise NotImplementedError(
        "TODO: call universe.builder.build_training_universe, persist to DB"
    )


@asset(
    group_name="discovery",
    deps=["candidate_universe"],
    description=(
        "Narrower scoring universe: AI/crypto/software/semiconductor/fintech/etc. "
        "plus always_include tickers."
    ),
)
def scoring_universe(
    context: AssetExecutionContext,
    postgres: PostgresResource,
) -> None:
    raise NotImplementedError(
        "TODO: call universe.builder.build_scoring_universe, persist to DB"
    )
