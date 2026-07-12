"""Hash-addressed local cache for SEC bulk companyfacts ZIP archive.

Archives are stored at <cache_dir>/archives/<sha256>.zip and NEVER deleted.
A current.json manifest tracks which archive is current plus HTTP caching headers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_TRANSIENT_CODES = frozenset({429, 500, 502, 503, 504})
_BASE_BACKOFF = 0.5
_MAX_BACKOFF = 60.0


@dataclass(frozen=True)
class CachedArchive:
    path: Path
    sha256: str
    byte_size: int
    downloaded_at: datetime
    was_refreshed: bool


class SecBulkArchiveCache:
    """Manages a hash-addressed local cache of the SEC companyfacts ZIP.

    Call get_or_refresh() to obtain the current archive path.  Old archives
    are retained indefinitely — never deleted.
    """

    def __init__(
        self,
        cache_dir: Path,
        companyfacts_bulk_url: str,
        user_agent: str,
        remote_check_after_hours: float = 24.0,
        download_timeout_seconds: float = 300.0,
        max_retries: int = 4,
    ) -> None:
        self._cache_dir = cache_dir
        self._archives_dir = cache_dir / "archives"
        self._manifest_path = cache_dir / "current.json"
        self._url = companyfacts_bulk_url
        self._user_agent = user_agent
        self._remote_check_after_hours = remote_check_after_hours
        self._timeout = download_timeout_seconds
        self._max_retries = max_retries

    def get_or_refresh(
        self,
        *,
        force_refresh: bool = False,
        cache_only: bool = False,
    ) -> CachedArchive:
        """Return the current companyfacts archive, downloading if needed.

        Parameters
        ----------
        force_refresh:
            Skip staleness check and always issue a download request.
        cache_only:
            Never make network requests; raise if no valid cached archive exists.
        """
        manifest = self._read_manifest()

        if not force_refresh and manifest:
            cached = self._validate_cached(manifest)
            if cached is not None:
                if cache_only:
                    return cached
                downloaded_at = datetime.fromisoformat(manifest["downloaded_at"])
                age_hours = (datetime.now(tz=UTC) - downloaded_at).total_seconds() / 3600
                if age_hours < self._remote_check_after_hours:
                    logger.debug(
                        "CompanyFacts archive is fresh (%.1f h < %.1f h threshold), using cache",
                        age_hours, self._remote_check_after_hours,
                    )
                    return cached
                # Stale — do conditional request
                return self._conditional_refresh(cached, manifest)

        if cache_only:
            raise RuntimeError(
                f"cache_only=True but no valid cached archive found at {self._cache_dir}"
            )

        return self._full_download()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _read_manifest(self) -> dict | None:
        if not self._manifest_path.exists():
            return None
        try:
            return json.loads(self._manifest_path.read_text())
        except Exception as exc:
            logger.warning("Could not read cache manifest %s: %s", self._manifest_path, exc)
            return None

    def _validate_cached(self, manifest: dict) -> CachedArchive | None:
        sha256 = manifest.get("sha256", "")
        if not sha256:
            return None
        archive_path = self._archives_dir / f"{sha256}.zip"
        if not archive_path.exists() or archive_path.stat().st_size == 0:
            return None
        if not zipfile.is_zipfile(archive_path):
            logger.warning("Cached archive %s failed ZIP validation, will re-download", archive_path)
            return None
        return CachedArchive(
            path=archive_path,
            sha256=sha256,
            byte_size=manifest.get("byte_size", 0),
            downloaded_at=datetime.fromisoformat(manifest["downloaded_at"]),
            was_refreshed=False,
        )

    def _conditional_refresh(self, cached: CachedArchive, manifest: dict) -> CachedArchive:
        """Issue a conditional GET; return cached if 304, or store new archive."""
        logger.info("Checking for updated SEC companyfacts archive (conditional GET)")
        headers = self._base_headers()
        etag = manifest.get("etag")
        last_modified = manifest.get("last_modified")
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        result = self._download_with_retry(headers)
        if result is None:
            # 304 Not Modified — bump downloaded_at in manifest so we don't check again soon
            updated_manifest = {**manifest, "downloaded_at": datetime.now(tz=UTC).isoformat()}
            self._write_manifest(updated_manifest)
            logger.info("SEC companyfacts archive unchanged (304)")
            return CachedArchive(
                path=cached.path,
                sha256=cached.sha256,
                byte_size=cached.byte_size,
                downloaded_at=datetime.now(tz=UTC),
                was_refreshed=False,
            )
        return result

    def _full_download(self) -> CachedArchive:
        logger.info("Downloading SEC companyfacts archive from %s", self._url)
        result = self._download_with_retry(self._base_headers())
        if result is None:
            raise RuntimeError("Unexpected 304 on unconditional download")
        return result

    def _base_headers(self) -> dict:
        return {
            "User-Agent": self._user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    def _download_with_retry(self, headers: dict) -> CachedArchive | None:
        """Download the archive.  Returns None on 304.  Raises on fatal errors."""
        import time

        self._archives_dir.mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(connect=30.0, read=self._timeout, write=30.0, pool=10.0)
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = min(_BASE_BACKOFF * (2 ** (attempt - 1)), _MAX_BACKOFF)
                logger.info("Retry %d/%d, sleeping %.1fs", attempt, self._max_retries, delay)
                time.sleep(delay)

            tmp_path: Path | None = None
            try:
                with httpx.Client(follow_redirects=True, timeout=timeout) as http:
                    with http.stream("GET", self._url, headers=headers) as resp:
                        if resp.status_code == 304:
                            return None

                        if resp.status_code in _TRANSIENT_CODES:
                            last_error = RuntimeError(f"HTTP {resp.status_code}")
                            if attempt == self._max_retries:
                                raise last_error
                            continue

                        if resp.status_code != 200:
                            raise RuntimeError(
                                f"HTTP {resp.status_code} (permanent) from {self._url}"
                            )

                        etag = resp.headers.get("etag")
                        last_modified = resp.headers.get("last-modified")

                        tmp_path = self._archives_dir / f"_download_tmp_{attempt}.zip"
                        hasher = hashlib.sha256()
                        byte_size = 0
                        with tmp_path.open("wb") as fh:
                            for chunk in resp.iter_bytes(chunk_size=65_536):
                                fh.write(chunk)
                                hasher.update(chunk)
                                byte_size += len(chunk)

                sha256 = hasher.hexdigest()

                if not zipfile.is_zipfile(tmp_path):
                    raise RuntimeError(
                        f"Downloaded file is not a valid ZIP (sha256={sha256}, bytes={byte_size})"
                    )

                final_path = self._archives_dir / f"{sha256}.zip"
                if final_path.exists():
                    tmp_path.unlink()
                    logger.info(
                        "Downloaded archive matches existing %s (%.16s)", final_path.name, sha256
                    )
                else:
                    tmp_path.rename(final_path)
                    logger.info(
                        "Stored new companyfacts archive: sha256=%.16s bytes=%d", sha256, byte_size
                    )
                tmp_path = None

                downloaded_at = datetime.now(tz=UTC)
                self._write_manifest({
                    "sha256": sha256,
                    "byte_size": byte_size,
                    "downloaded_at": downloaded_at.isoformat(),
                    "etag": etag,
                    "last_modified": last_modified,
                })
                return CachedArchive(
                    path=final_path,
                    sha256=sha256,
                    byte_size=byte_size,
                    downloaded_at=downloaded_at,
                    was_refreshed=True,
                )

            except (RuntimeError, zipfile.BadZipFile):
                _cleanup(tmp_path)
                raise
            except httpx.TransportError as exc:
                _cleanup(tmp_path)
                last_error = exc
                if attempt == self._max_retries:
                    raise RuntimeError(
                        f"Transport error after {attempt + 1} attempts: {exc}"
                    ) from exc
                continue

        raise RuntimeError(
            f"All {self._max_retries + 1} attempts failed. Last error: {last_error}"
        )

    def _write_manifest(self, data: dict) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self._manifest_path)


def _cleanup(path: Path | None) -> None:
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            pass
