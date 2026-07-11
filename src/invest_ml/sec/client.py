"""SEC EDGAR bulk-data HTTP client.

Responsible only for reliable transport: download, retry, hash, atomic rename.
Parsing and persistence happen in separate modules.

No HTTP requests occur at module import time.
"""

import hashlib
import logging
import random
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# HTTP status codes that warrant a retry.
_TRANSIENT_CODES = frozenset({429, 500, 502, 503, 504})

# Backoff parameters.
_BASE_BACKOFF_SECONDS = 0.5
_MAX_BACKOFF_SECONDS = 60.0
_JITTER_SECONDS = 2.0


# ── Domain exceptions ────────────────────────────────────────────────────────


class SecDownloadError(Exception):
    """HTTP or network failure while downloading from SEC."""


class SecInvalidArchiveError(Exception):
    """Downloaded file failed ZIP integrity check."""


class SecConfigurationError(Exception):
    """Missing or invalid SecClient configuration."""


# ── Result type ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    sha256: str
    byte_size: int
    downloaded_at: datetime
    etag: str | None
    last_modified: str | None
    not_modified: bool


# ── Client ───────────────────────────────────────────────────────────────────


class SecClient:
    """Downloads SEC EDGAR bulk data archives.

    Instantiation does NOT open any connections.
    """

    def __init__(
        self,
        submissions_bulk_url: str,
        user_agent: str,
        download_timeout_seconds: float = 300.0,
        max_retries: int = 4,
        max_zip_member_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        user_agent = (user_agent or "").strip()
        if not user_agent:
            raise SecConfigurationError(
                "SEC_USER_AGENT must not be empty. "
                "SEC Fair Access policy requires 'AppName contact@email.com'."
            )
        if "your-email@example.com" in user_agent:
            raise SecConfigurationError(
                "SEC_USER_AGENT contains the placeholder 'your-email@example.com'. "
                "Replace it with a real contact email before use."
            )
        self._url = submissions_bulk_url
        self._user_agent = user_agent
        self._timeout_seconds = download_timeout_seconds
        self._max_retries = max_retries
        self._max_zip_member_bytes = max_zip_member_bytes

    # ── Public API ───────────────────────────────────────────────────────────

    def download_submissions_archive(
        self,
        destination_dir: Path,
        *,
        previous_etag: str | None = None,
        previous_last_modified: str | None = None,
        _sleep_fn: Callable[[float], None] = time.sleep,
    ) -> DownloadResult:
        """Download the bulk submissions ZIP.  Thin wrapper around download_archive."""
        return self.download_archive(
            self._url,
            destination_dir,
            "submissions.zip",
            previous_etag=previous_etag,
            previous_last_modified=previous_last_modified,
            _sleep_fn=_sleep_fn,
        )

    def download_archive(
        self,
        url: str,
        destination_dir: Path,
        filename: str,
        *,
        previous_etag: str | None = None,
        previous_last_modified: str | None = None,
        _sleep_fn: Callable[[float], None] = time.sleep,
    ) -> DownloadResult:
        """Download any SEC bulk ZIP archive to destination_dir.

        Streams to a temporary file, computes SHA-256 incrementally, then
        atomically renames the file.  Retries transient failures with
        exponential backoff and honours Retry-After when present.

        Parameters
        ----------
        url:
            Full URL of the ZIP archive to download.
        destination_dir:
            Directory where the file will be written.
        filename:
            Final filename inside destination_dir (e.g. ``submissions.zip``).
        previous_etag / previous_last_modified:
            Supply to send a conditional request.  A 304 response returns
            DownloadResult with not_modified=True and no file is written.
        _sleep_fn:
            Injectable sleep function; replace with a no-op in tests.
        """
        destination_dir.mkdir(parents=True, exist_ok=True)

        headers = {
            "User-Agent": self._user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        if previous_etag:
            headers["If-None-Match"] = previous_etag
        if previous_last_modified:
            headers["If-Modified-Since"] = previous_last_modified

        timeout = httpx.Timeout(
            connect=30.0,
            read=float(self._timeout_seconds),
            write=30.0,
            pool=10.0,
        )

        stem = filename[: filename.rfind(".")] if "." in filename else filename
        last_error: Exception | None = None
        retry_after_override: float | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = (
                    retry_after_override
                    if retry_after_override is not None
                    else self._backoff_delay(attempt - 1)
                )
                retry_after_override = None
                logger.info(
                    "SEC download retry %d/%d, sleeping %.1fs (url=%s)",
                    attempt, self._max_retries, delay, url,
                )
                _sleep_fn(delay)

            tmp_path: Path | None = None
            try:
                with httpx.Client(follow_redirects=True, timeout=timeout) as http:
                    with http.stream("GET", url, headers=headers) as resp:
                        if resp.status_code == 304:
                            logger.info("SEC archive not modified (304), url=%s", url)
                            return DownloadResult(
                                path=Path(""),
                                sha256="",
                                byte_size=0,
                                downloaded_at=datetime.now(tz=UTC),
                                etag=resp.headers.get("etag"),
                                last_modified=resp.headers.get("last-modified"),
                                not_modified=True,
                            )

                        if resp.status_code in _TRANSIENT_CODES:
                            raw_ra = resp.headers.get("retry-after")
                            if raw_ra:
                                try:
                                    retry_after_override = float(raw_ra)
                                except ValueError:
                                    pass
                            last_error = SecDownloadError(
                                f"HTTP {resp.status_code} from {url}"
                            )
                            if attempt == self._max_retries:
                                raise last_error
                            continue

                        if resp.status_code != 200:
                            raise SecDownloadError(
                                f"HTTP {resp.status_code} (permanent) from {url}"
                            )

                        etag = resp.headers.get("etag")
                        last_modified = resp.headers.get("last-modified")

                        tmp_path = destination_dir / f"{stem}_tmp_{attempt}.zip"
                        hasher = hashlib.sha256()
                        byte_size = 0
                        t0 = time.monotonic()

                        with tmp_path.open("wb") as fh:
                            for chunk in resp.iter_bytes(chunk_size=65_536):
                                fh.write(chunk)
                                hasher.update(chunk)
                                byte_size += len(chunk)

                        duration = time.monotonic() - t0
                        sha256 = hasher.hexdigest()

                        if not zipfile.is_zipfile(tmp_path):
                            raise SecInvalidArchiveError(
                                f"Downloaded file from {url} is not a valid ZIP "
                                f"(sha256={sha256}, bytes={byte_size})"
                            )

                        final_path = destination_dir / filename
                        tmp_path.rename(final_path)
                        tmp_path = None

                        logger.info(
                            "Downloaded SEC archive: url=%s bytes=%d sha256=%.16s... "
                            "duration=%.1fs etag=%s",
                            url, byte_size, sha256, duration, etag,
                        )
                        return DownloadResult(
                            path=final_path,
                            sha256=sha256,
                            byte_size=byte_size,
                            downloaded_at=datetime.now(tz=UTC),
                            etag=etag,
                            last_modified=last_modified,
                            not_modified=False,
                        )

            except (SecInvalidArchiveError, SecDownloadError):
                _cleanup(tmp_path)
                raise

            except httpx.TransportError as exc:
                _cleanup(tmp_path)
                last_error = exc
                if attempt == self._max_retries:
                    raise SecDownloadError(
                        f"Transport error after {attempt + 1} attempts: {exc}"
                    ) from exc
                logger.warning("Transport error on attempt %d: %s", attempt + 1, exc)
                continue

        raise SecDownloadError(
            f"All {self._max_retries + 1} attempts failed. Last error: {last_error}"
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        base = min(_BASE_BACKOFF_SECONDS * (2**attempt), _MAX_BACKOFF_SECONDS)
        jitter = random.uniform(0.0, _JITTER_SECONDS)
        return base + jitter


def _cleanup(path: Path | None) -> None:
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            pass
