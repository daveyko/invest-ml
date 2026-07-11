"""Tiingo HTTP client with retry/backoff and credential-safe logging."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from invest_ml.market.errors import (
    MarketDataAuthenticationError,
    MarketDataEntitlementError,
    MarketDataInstrumentNotFoundError,
    MarketDataInvalidResponseError,
    MarketDataQuotaExhaustedError,
    MarketDataRateLimitError,
    MarketDataTemporaryError,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 1.0
_DEFAULT_BACKOFF_MAX = 60.0


class TiingoHttpClient:
    """Synchronous httpx client for Tiingo REST endpoints.

    The API token is sent only as an Authorization header and is never
    logged or embedded in URLs.
    """

    def __init__(
        self,
        api_token: str,
        base_url: str = "https://api.tiingo.com",
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        backoff_max: float = _DEFAULT_BACKOFF_MAX,
        *,
        sleeper: Callable[[float], None] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._sleep = sleeper if sleeper is not None else time.sleep
        self._headers = {
            "Authorization": f"Token {api_token}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    def _sanitized_url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self._sanitized_url(path)
        attempt = 0
        last_exc: Exception | None = None

        while attempt <= self._max_retries:
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(
                        url,
                        headers=self._headers,
                        params=params,
                    )
                return self._handle_response(response, path)
            except (MarketDataRateLimitError, MarketDataTemporaryError) as exc:
                last_exc = exc
                if attempt >= self._max_retries:
                    break
                wait = self._retry_wait(exc, attempt)
                logger.warning(
                    "Tiingo request to %s failed (%s); retrying in %.1fs (attempt %d/%d)",
                    path,
                    type(exc).__name__,
                    wait,
                    attempt + 1,
                    self._max_retries,
                )
                self._sleep(wait)
                attempt += 1
            except (
                MarketDataAuthenticationError,
                MarketDataEntitlementError,
                MarketDataInstrumentNotFoundError,
                MarketDataQuotaExhaustedError,
                MarketDataInvalidResponseError,
            ):
                raise
        raise last_exc  # type: ignore[misc]

    def _retry_wait(self, exc: Exception, attempt: int) -> float:
        if isinstance(exc, MarketDataRateLimitError) and exc.args:
            try:
                return float(exc.args[0])
            except (ValueError, TypeError):
                pass
        return min(self._backoff_base * (2**attempt), self._backoff_max)

    def _handle_response(self, response: httpx.Response, path: str) -> Any:
        if response.status_code == 200:
            try:
                return response.json()
            except Exception as exc:
                raise MarketDataInvalidResponseError(
                    f"Non-JSON response from {path}"
                ) from exc

        if response.status_code == 401:
            raise MarketDataAuthenticationError(
                "Tiingo authentication failed (401) — check TIINGO_API_TOKEN"
            )

        if response.status_code == 403:
            raise MarketDataEntitlementError(
                f"Tiingo entitlement error (403) for {path}"
            )

        if response.status_code == 404:
            raise MarketDataInstrumentNotFoundError(
                f"Tiingo returned 404 for {path}"
            )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                    raise MarketDataRateLimitError(wait)
                except (ValueError, TypeError):
                    pass
            raise MarketDataRateLimitError("Tiingo rate limit exceeded (429)")

        if response.status_code == 402:
            raise MarketDataQuotaExhaustedError(
                "Tiingo daily/hourly quota exceeded (402)"
            )

        if response.status_code >= 500:
            raise MarketDataTemporaryError(
                f"Tiingo server error {response.status_code} for {path}"
            )

        raise MarketDataInvalidResponseError(
            f"Unexpected Tiingo status {response.status_code} for {path}"
        )
