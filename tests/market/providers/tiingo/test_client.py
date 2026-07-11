"""Unit tests for TiingoHttpClient — no live network requests."""

from unittest.mock import MagicMock, patch

import pytest

from invest_ml.market.errors import (
    MarketDataAuthenticationError,
    MarketDataEntitlementError,
    MarketDataInstrumentNotFoundError,
    MarketDataRateLimitError,
    MarketDataTemporaryError,
)
from invest_ml.market.providers.tiingo.client import TiingoHttpClient


def _mock_response(status_code: int, json_body=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


def _client(sleeper=None):
    return TiingoHttpClient(
        api_token="test-token",
        max_retries=2,
        sleeper=sleeper or (lambda _: None),
    )


@patch("invest_ml.market.providers.tiingo.client.httpx.Client")
def test_200_returns_json(mock_httpx_client):
    expected = [{"ticker": "ACME"}]
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.get.return_value = _mock_response(200, json_body=expected)
    mock_httpx_client.return_value = ctx

    client = _client()
    result = client.get("/tiingo/daily/ACME")
    assert result == expected


@patch("invest_ml.market.providers.tiingo.client.httpx.Client")
def test_401_raises_auth_error(mock_httpx_client):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.get.return_value = _mock_response(401)
    mock_httpx_client.return_value = ctx

    client = _client()
    with pytest.raises(MarketDataAuthenticationError):
        client.get("/tiingo/daily/ACME")


@patch("invest_ml.market.providers.tiingo.client.httpx.Client")
def test_403_raises_entitlement_error(mock_httpx_client):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.get.return_value = _mock_response(403)
    mock_httpx_client.return_value = ctx

    client = _client()
    with pytest.raises(MarketDataEntitlementError):
        client.get("/tiingo/fundamentals/ACME/daily")


@patch("invest_ml.market.providers.tiingo.client.httpx.Client")
def test_404_raises_not_found(mock_httpx_client):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.get.return_value = _mock_response(404)
    mock_httpx_client.return_value = ctx

    client = _client()
    with pytest.raises(MarketDataInstrumentNotFoundError):
        client.get("/tiingo/daily/FAKEFAKE")


@patch("invest_ml.market.providers.tiingo.client.httpx.Client")
def test_500_retries_then_raises(mock_httpx_client):
    slept = []
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.get.return_value = _mock_response(500)
    mock_httpx_client.return_value = ctx

    client = _client(sleeper=slept.append)
    with pytest.raises(MarketDataTemporaryError):
        client.get("/tiingo/daily/ACME/prices")

    assert len(slept) == 2  # max_retries=2


@patch("invest_ml.market.providers.tiingo.client.httpx.Client")
def test_429_uses_retry_after(mock_httpx_client):
    slept = []
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.get.return_value = _mock_response(429, headers={"Retry-After": "5"})
    mock_httpx_client.return_value = ctx

    client = _client(sleeper=slept.append)
    with pytest.raises(MarketDataRateLimitError):
        client.get("/tiingo/daily/ACME/prices")

    assert 5.0 in slept


@patch("invest_ml.market.providers.tiingo.client.httpx.Client")
def test_auth_header_not_in_params(mock_httpx_client):
    """Token must never be passed as a query parameter."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.get.return_value = _mock_response(200, json_body=[])
    mock_httpx_client.return_value = ctx

    client = _client()
    client.get("/tiingo/daily/ACME/prices", params={"startDate": "2023-01-01"})

    call_kwargs = ctx.get.call_args
    params_passed = call_kwargs.kwargs.get("params", {}) or {}
    assert "token" not in params_passed
    assert "apiKey" not in params_passed

    headers_passed = call_kwargs.kwargs.get("headers", {}) or {}
    assert any("Token" in str(v) for v in headers_passed.values())
