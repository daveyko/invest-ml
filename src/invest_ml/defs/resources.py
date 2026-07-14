"""Dagster ConfigurableResource definitions.

No database connections or network requests are made at import time.
Resources connect lazily when first used inside an asset execution context.
"""

import hashlib
import logging
from pathlib import Path

from dagster import ConfigurableResource, EnvVar
from pydantic import Field
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


class PostgresResource(ConfigurableResource):
    """SQLAlchemy engine + session factory backed by PostgreSQL."""

    database_url: str = EnvVar("DATABASE_URL")

    def get_engine(self) -> Engine:
        return create_engine(self.database_url, pool_pre_ping=True)

    def get_session_factory(self) -> sessionmaker[Session]:
        return sessionmaker(self.get_engine(), expire_on_commit=False)


class SecBulkResource(ConfigurableResource):
    """Configuration for SEC EDGAR bulk data.

    No downloads occur during initialization or Dagster definition load.
    """

    # Bulk submissions archive (the single ZIP containing all company records).
    submissions_bulk_url: str = Field(
        default="https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip",
        description="URL of the SEC bulk submissions ZIP archive.",
    )
    # Bulk companyfacts archive used by the companyfacts_data_profiles asset.
    companyfacts_bulk_url: str = Field(
        default="https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
        description="URL of the SEC bulk companyfacts ZIP archive.",
    )
    # Per-company CompanyFacts API endpoint (used by later assets).
    companyfacts_url: str = "https://data.sec.gov/api/xbrl/companyfacts/"
    # Version tag stamped on every CompanyDataProfile row produced by this run.
    companyfacts_profile_version: str = Field(
        default="companyfacts_profile_v1",
        description="Profile version string written to company_data_profiles.profile_version.",
    )

    # Required by SEC Fair Access policy: "App-name Contact-email"
    user_agent: str = EnvVar("SEC_USER_AGENT")

    download_dir: str = Field(
        default="var/downloads/sec",
        description="Local directory for downloaded SEC submissions archive.",
    )
    download_timeout_seconds: int = Field(
        default=300,
        description="Total read timeout for streaming SEC archives.",
    )
    max_retries: int = Field(
        default=4, description="Max retry attempts on transient errors."
    )
    max_zip_member_bytes: int = Field(
        default=50 * 1024 * 1024,  # 50 MB per member
        description="Maximum allowed size of a single ZIP member in bytes.",
    )

    # CompanyFacts archive cache settings
    companyfacts_cache_dir: str = Field(
        default="var/cache/sec/companyfacts",
        description="Directory for the hash-addressed companyfacts ZIP archive cache.",
    )
    companyfacts_remote_check_after_hours: float = Field(
        default=24.0,
        description="Hours before re-checking SEC for an updated companyfacts archive.",
    )
    force_refresh: bool = Field(
        default=False,
        description="Force download of a fresh companyfacts archive on every run.",
    )
    cache_only: bool = Field(
        default=False,
        description="Never make network requests; fail if no valid cached archive exists.",
    )

    def make_client(self):  # type: ignore[no-untyped-def]
        from invest_ml.sec.client import SecClient

        return SecClient(
            submissions_bulk_url=self.submissions_bulk_url,
            user_agent=self.user_agent,
            download_timeout_seconds=self.download_timeout_seconds,
            max_retries=self.max_retries,
            max_zip_member_bytes=self.max_zip_member_bytes,
        )

    def make_archive_cache(self):  # type: ignore[no-untyped-def]
        from invest_ml.sec.archive_cache import SecBulkArchiveCache

        return SecBulkArchiveCache(
            cache_dir=Path(self.companyfacts_cache_dir),
            companyfacts_bulk_url=self.companyfacts_bulk_url,
            user_agent=self.user_agent,
            remote_check_after_hours=self.companyfacts_remote_check_after_hours,
            download_timeout_seconds=float(self.download_timeout_seconds),
            max_retries=self.max_retries,
        )

    @property
    def download_dir_path(self) -> Path:
        return Path(self.download_dir)

    def find_cached_archive(
        self,
        expected_sha256: str | None,
        *,
        filename: str = "submissions.zip",
    ) -> Path | None:
        """Return the local archive path if it exists and its SHA-256 matches.

        Returns None if the file is absent, empty, or the hash differs.
        Designed to be swapped for object-storage (S3/GCS) in a future
        implementation — callers should treat the returned path as read-only.
        """
        if not expected_sha256:
            return None
        candidate = self.download_dir_path / filename
        if not candidate.exists() or candidate.stat().st_size == 0:
            return None
        actual = _sha256_file(candidate)
        if actual != expected_sha256:
            logger.debug(
                "Local archive %s hash mismatch: expected=%.16s actual=%.16s",
                filename,
                expected_sha256,
                actual,
            )
            return None
        return candidate


class ArtifactStoreResource(ConfigurableResource):
    """Local filesystem artifact store. No cloud dependency."""

    root_path: str = Field(default="var")

    @property
    def raw_dir(self) -> Path:
        return Path(self.root_path) / "raw"

    @property
    def datasets_dir(self) -> Path:
        return Path(self.root_path) / "datasets"

    @property
    def models_dir(self) -> Path:
        return Path(self.root_path) / "models"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.datasets_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)


class EquityMarketDataResource(ConfigurableResource):
    """Configuration for market data fetching (price bars + optional market cap)."""

    market_data_provider: str = Field(default="tiingo")
    tiingo_api_token: str = EnvVar("TIINGO_API_TOKEN")
    tiingo_base_url: str = Field(default="https://api.tiingo.com")
    maximum_symbols_per_run: int = Field(default=2500)

    # EOD price-bar ingestion settings
    tiingo_eod_reference_ticker: str = Field(default="SPY")
    tiingo_eod_max_concurrency: int = Field(default=4)
    price_bars_backfill_start_date: str = Field(default="2015-01-01")
    price_bars_target_end_date: str = Field(
        default=""
    )  # empty = use provider watermark
    price_bars_incremental_overlap_days: int = Field(default=14)
    price_bar_security_batch_size: int = Field(default=25)
    price_bar_insert_batch_size: int = Field(default=10000)
    price_bar_max_failed_securities: int = Field(default=25)
    price_bar_max_failed_security_ratio: float = Field(default=0.02)

    def build_price_provider(self, symbol_overrides: dict | None = None):  # type: ignore[return]
        from invest_ml.market.providers.factory import create_price_provider

        return create_price_provider(
            provider_name=self.market_data_provider,
            api_token=self.tiingo_api_token,
            base_url=self.tiingo_base_url,
            symbol_overrides=symbol_overrides,
        )

    def build_daily_price_provider(self, symbol_overrides: dict | None = None):  # type: ignore[return]
        from invest_ml.market.providers.tiingo.client import TiingoHttpClient
        from invest_ml.market.providers.tiingo.daily_provider import (
            TiingoDailyPriceProvider,
        )

        http_client = TiingoHttpClient(
            api_token=self.tiingo_api_token,
            base_url=self.tiingo_base_url,
        )
        return TiingoDailyPriceProvider(
            http_client=http_client,
            symbol_overrides=symbol_overrides,
        )


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file by streaming it in 64 KB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()
