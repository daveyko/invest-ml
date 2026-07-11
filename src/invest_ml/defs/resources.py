"""Dagster ConfigurableResource definitions.

No database connections or network requests are made at import time.
Resources connect lazily when first used inside an asset execution context.
"""

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from dagster import ConfigurableResource, EnvVar
from pydantic import Field
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


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
        default="https://data.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
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
        description="Local directory for downloaded SEC archives.",
    )
    download_timeout_seconds: int = Field(
        default=300,
        description="Total read timeout for streaming the submissions archive.",
    )
    max_retries: int = Field(default=4, description="Max retry attempts on transient errors.")
    retain_archives: bool = Field(
        default=False,
        description="Keep the downloaded ZIP after processing (useful for debugging).",
    )
    max_zip_member_bytes: int = Field(
        default=50 * 1024 * 1024,  # 50 MB per member
        description="Maximum allowed size of a single ZIP member in bytes.",
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
                filename, expected_sha256, actual,
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


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file by streaming it in 64 KB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()
