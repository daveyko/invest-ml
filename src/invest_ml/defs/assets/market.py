"""Market-data Dagster assets.

Flow
----
active selected training securities
        ↓
one Tiingo provider watermark request
        ↓
database coverage + sync-state planning
        ↓
bounded per-ticker EOD requests (ThreadPoolExecutor)
        ↓
bulk correction-aware upsert
        ↓
price_bars
"""

import time
from datetime import UTC, date, datetime

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from invest_ml.defs.resources import EquityMarketDataResource, PostgresResource

_SOURCE = "tiingo_eod"
_TRAINING_UNIVERSE_NAME = "training"
_TRAINING_UNIVERSE_VERSION = "v1"


@asset(
    group_name="market_data",
    deps=["training_universe"],
    description=(
        "Daily raw + adjusted EOD price bars for all selected training-universe securities. "
        "Ingests historical data on first run, incrementally refreshes on subsequent runs, "
        "and performs targeted full-reconciliation when a corporate action is detected."
    ),
)
def selected_price_bars(
    context: AssetExecutionContext,
    postgres: PostgresResource,
    equity_market_data: EquityMarketDataResource,
) -> MaterializeResult:
    """Fetch and persist daily Tiingo EOD price bars for training-universe securities.

    Execution flow
    --------------
    1. Resolve as_of_date.
    2. Load selected training securities (one DB query).
    3. Create an ingestion run (status=running).
    4. Resolve the latest provider date using one watermark request or configured override.
    5. Query all existing coverage and sync states in bulk DB queries.
    6. Build a per-security request plan.
    7. Execute bounded concurrent Tiingo requests in security batches.
    8. Bulk-upsert returned bars; update sync states.
    9. Commit per batch.
    10. Mark ingestion run succeeded.
    11. On fatal infrastructure failure: mark run failed, re-raise.
    """
    from invest_ml.config.loaders import load_market_data_config
    from invest_ml.db.repositories.price_bars import PriceBarsRepository
    from invest_ml.market.errors import MarketDataError
    from invest_ml.market.price_bars.service import SelectedPriceBarsService

    session_factory = postgres.get_session_factory()
    as_of_date = date.today()
    t_start = time.monotonic()

    market_cfg = load_market_data_config()
    symbol_overrides = market_cfg.get("market_data", {}).get("symbol_overrides", {})

    # ── Load selected training securities ────────────────────────────────────
    with session_factory() as session:
        repo = PriceBarsRepository(session)
        selected = repo.list_selected_training_securities(
            universe_name=_TRAINING_UNIVERSE_NAME,
            universe_version=_TRAINING_UNIVERSE_VERSION,
            as_of_date=as_of_date,
        )

    context.log.info(
        "selected_price_bars: %d training securities selected as of %s",
        len(selected),
        as_of_date,
    )

    # ── Create ingestion run ──────────────────────────────────────────────────
    with session_factory() as session:
        repo = PriceBarsRepository(session)
        run = repo.create_ingestion_run(
            source=_SOURCE,
            source_uri="tiingo_eod",
            started_at=datetime.now(tz=UTC),
        )
        session.commit()
        run_id = run.run_id

    context.log.info("Created IngestionRun %s for selected_price_bars", run_id)

    try:
        # ── Resolve target end date ───────────────────────────────────────────
        override_str = equity_market_data.price_bars_target_end_date.strip()
        if override_str:
            target_end_date = date.fromisoformat(override_str)
            context.log.info(
                "Using configured PRICE_BARS_TARGET_END_DATE override: %s", target_end_date
            )
            reference_ticker = equity_market_data.tiingo_eod_reference_ticker
        else:
            reference_ticker = equity_market_data.tiingo_eod_reference_ticker
            daily_provider = equity_market_data.build_daily_price_provider(symbol_overrides)
            try:
                provider_latest = daily_provider.get_latest_available_date(
                    reference_ticker=reference_ticker
                )
            except MarketDataError as exc:
                raise RuntimeError(
                    f"Provider watermark request for {reference_ticker!r} failed: {exc}. "
                    "Aborting to avoid sending thousands of stale requests."
                ) from exc
            target_end_date = min(provider_latest, as_of_date)
            context.log.info(
                "Provider watermark %r → latest=%s, target_end_date=%s",
                reference_ticker,
                provider_latest,
                target_end_date,
            )

        backfill_start_date = date.fromisoformat(
            equity_market_data.price_bars_backfill_start_date
        )

        # ── Run the service ───────────────────────────────────────────────────
        daily_provider = equity_market_data.build_daily_price_provider(symbol_overrides)
        service = SelectedPriceBarsService(
            price_provider=daily_provider,
            session_factory=session_factory,
        )

        result = service.materialize(
            selected_securities=selected,
            target_end_date=target_end_date,
            backfill_start_date=backfill_start_date,
            ingestion_run_id=run_id,
            max_concurrency=equity_market_data.tiingo_eod_max_concurrency,
            security_batch_size=equity_market_data.price_bar_security_batch_size,
            insert_batch_size=equity_market_data.price_bar_insert_batch_size,
            incremental_overlap_days=equity_market_data.price_bars_incremental_overlap_days,
            source=_SOURCE,
            max_failed_securities=equity_market_data.price_bar_max_failed_securities,
            max_failed_security_ratio=equity_market_data.price_bar_max_failed_security_ratio,
        )

        # ── Mark run succeeded ────────────────────────────────────────────────
        with session_factory() as session:
            repo = PriceBarsRepository(session)
            repo.succeed_ingestion_run(
                run_id,
                entities_checked=result.securities_requested + result.securities_skipped,
                entities_changed=result.rows_inserted + result.rows_updated,
                metadata={
                    "provider": {
                        "name": _SOURCE,
                        "reference_ticker": reference_ticker,
                        "target_end_date": target_end_date.isoformat(),
                        "requests": result.provider_requests,
                        "retries": result.provider_retries,
                    },
                    "selection": {"selected_securities": len(selected)},
                    "plan": {
                        "already_current": result.plan.securities_already_current,
                        "initial_backfills": result.plan.securities_requiring_initial_backfill,
                        "incremental_updates": result.plan.securities_requiring_incremental_update,
                        "full_reconciliations": result.plan.securities_requiring_full_reconciliation,
                        "estimated_requests": result.plan.estimated_provider_requests,
                    },
                    "result": {
                        "securities_succeeded": result.securities_succeeded,
                        "securities_failed": result.securities_failed,
                        "rows_inserted": result.rows_inserted,
                        "rows_updated": result.rows_updated,
                        "rows_unchanged": result.rows_unchanged,
                    },
                },
            )
            session.commit()

    except Exception as exc:
        with session_factory() as session:
            repo = PriceBarsRepository(session)
            repo.fail_ingestion_run(run_id, error=str(exc))
            session.commit()
        raise

    duration = time.monotonic() - t_start
    plan = result.plan

    context.log.info(
        "selected_price_bars complete: requested=%d succeeded=%d failed=%d "
        "inserted=%d updated=%d duration=%.1fs",
        result.securities_requested,
        result.securities_succeeded,
        result.securities_failed,
        result.rows_inserted,
        result.rows_updated,
        duration,
    )

    return MaterializeResult(
        metadata={
            "as_of_date": MetadataValue.text(as_of_date.isoformat()),
            "backfill_start_date": MetadataValue.text(
                equity_market_data.price_bars_backfill_start_date
            ),
            "target_provider_date": MetadataValue.text(target_end_date.isoformat()),
            "reference_ticker": MetadataValue.text(reference_ticker),
            # Plan
            "selected_securities": MetadataValue.int(plan.selected_securities),
            "securities_with_no_history": MetadataValue.int(
                plan.securities_requiring_initial_backfill
            ),
            "securities_with_partial_history": MetadataValue.int(
                plan.securities_requiring_full_reconciliation
            ),
            "securities_already_current": MetadataValue.int(plan.securities_already_current),
            "securities_incremental": MetadataValue.int(
                plan.securities_requiring_incremental_update
            ),
            "securities_full_reconciliation": MetadataValue.int(
                plan.securities_requiring_full_reconciliation
            ),
            "securities_retry_deferred": MetadataValue.int(plan.securities_retry_deferred),
            "estimated_provider_requests": MetadataValue.int(plan.estimated_provider_requests),
            # Execution
            "actual_provider_requests": MetadataValue.int(result.provider_requests),
            "provider_retries": MetadataValue.int(result.provider_retries),
            "provider_rate_limits": MetadataValue.int(result.provider_rate_limits),
            "securities_requested": MetadataValue.int(result.securities_requested),
            "securities_skipped": MetadataValue.int(result.securities_skipped),
            "securities_succeeded": MetadataValue.int(result.securities_succeeded),
            "securities_failed": MetadataValue.int(result.securities_failed),
            "securities_unsupported": MetadataValue.int(result.securities_unsupported),
            "initial_backfills": MetadataValue.int(result.initial_backfills),
            "incremental_updates": MetadataValue.int(result.incremental_updates),
            "full_reconciliations": MetadataValue.int(result.full_reconciliations),
            # Data
            "bars_received": MetadataValue.int(result.bars_received),
            "rows_inserted": MetadataValue.int(result.rows_inserted),
            "rows_updated": MetadataValue.int(result.rows_updated),
            "rows_unchanged": MetadataValue.int(result.rows_unchanged),
            "invalid_rows": MetadataValue.int(result.invalid_rows),
            "earliest_bar_date": MetadataValue.text(
                result.earliest_bar_date.isoformat() if result.earliest_bar_date else ""
            ),
            "latest_bar_date": MetadataValue.text(
                result.latest_bar_date.isoformat() if result.latest_bar_date else ""
            ),
            "duration_seconds": MetadataValue.float(round(duration, 1)),
        }
    )
