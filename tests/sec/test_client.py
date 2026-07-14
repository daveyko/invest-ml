"""Unit tests for SecClient.

All HTTP calls are mocked; no real network activity occurs.
No file writes happen outside pytest's tmp_path fixture.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from invest_ml.sec.client import (
    SecClient,
    SecConfigurationError,
    SecDownloadError,
    SecInvalidArchiveError,
)

_TEST_URL = "https://example.com/submissions.zip"
_TEST_UA = "TestSuite test@test.example.com"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_client(**kw) -> SecClient:
    return SecClient(
        submissions_bulk_url=kw.pop("url", _TEST_URL),
        user_agent=kw.pop("user_agent", _TEST_UA),
        download_timeout_seconds=10,
        max_retries=kw.pop("max_retries", 2),
        **kw,
    )


def _minimal_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("placeholder.txt", b"ok")
    return buf.getvalue()


def _mock_http(mock_response: MagicMock) -> MagicMock:
    """Wrap a mock response in the nested context-manager structure httpx uses."""
    stream_ctx = MagicMock()
    stream_ctx.__enter__ = MagicMock(return_value=mock_response)
    stream_ctx.__exit__ = MagicMock(return_value=False)

    http = MagicMock()
    http.__enter__ = MagicMock(return_value=http)
    http.__exit__ = MagicMock(return_value=False)
    http.stream.return_value = stream_ctx
    return http


def _resp(status: int, headers: dict | None = None, chunks: list[bytes] | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    if chunks is not None:
        r.iter_bytes = MagicMock(return_value=iter(chunks))
    return r


# ── Configuration validation ─────────────────────────────────────────────────


def test_empty_user_agent_raises():
    with pytest.raises(SecConfigurationError, match="must not be empty"):
        SecClient(_TEST_URL, user_agent="", max_retries=0)


def test_whitespace_user_agent_raises():
    with pytest.raises(SecConfigurationError, match="must not be empty"):
        SecClient(_TEST_URL, user_agent="   ", max_retries=0)


def test_placeholder_email_raises():
    with pytest.raises(SecConfigurationError, match="placeholder"):
        SecClient(_TEST_URL, user_agent="MyApp your-email@example.com", max_retries=0)


def test_valid_user_agent_succeeds():
    client = _make_client()
    assert client is not None


# ── No import-time network activity ──────────────────────────────────────────


def test_no_import_time_requests():
    import invest_ml.sec.client  # noqa: F401 — just importing must not call network


# ── HTTP 304 Not Modified ─────────────────────────────────────────────────────


def test_304_returns_not_modified_result(tmp_path):
    r = _resp(304, headers={"etag": '"v2"', "last-modified": "Thu, 01 Jan 2026 00:00:00 GMT"})
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=_mock_http(r)):
        result = client.download_submissions_archive(
            tmp_path, previous_etag='"v1"', _sleep_fn=lambda _: None
        )

    assert result.not_modified is True
    assert result.path == Path("")
    assert result.byte_size == 0
    assert result.sha256 == ""
    assert result.etag == '"v2"'


def test_304_does_not_write_file(tmp_path):
    r = _resp(304)
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=_mock_http(r)):
        client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)

    assert not list(tmp_path.glob("*.zip"))


# ── Successful 200 download ──────────────────────────────────────────────────


def test_200_writes_file_and_returns_correct_hash(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    expected_sha256 = hashlib.sha256(zip_bytes).hexdigest()
    r = _resp(200, headers={"etag": '"abc"'}, chunks=[zip_bytes])
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=_mock_http(r)):
        result = client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)

    assert result.not_modified is False
    assert result.sha256 == expected_sha256
    assert result.byte_size == len(zip_bytes)
    assert result.path == tmp_path / "submissions.zip"
    assert result.path.exists()
    assert result.etag == '"abc"'


def test_200_multi_chunk_hash_is_correct(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    half = len(zip_bytes) // 2
    chunks = [zip_bytes[:half], zip_bytes[half:]]
    expected = hashlib.sha256(zip_bytes).hexdigest()
    r = _resp(200, chunks=chunks)
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=_mock_http(r)):
        result = client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)

    assert result.sha256 == expected


def test_user_agent_header_is_sent(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    r = _resp(200, chunks=[zip_bytes])
    mock_http = _mock_http(r)
    client = _make_client(user_agent="MyApp contact@myapp.example.com")

    with patch("invest_ml.sec.client.httpx.Client", return_value=mock_http):
        client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)

    _, call_kwargs = mock_http.stream.call_args
    assert call_kwargs["headers"]["User-Agent"] == "MyApp contact@myapp.example.com"


def test_conditional_request_sends_if_none_match(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    r = _resp(200, chunks=[zip_bytes])
    mock_http = _mock_http(r)
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=mock_http):
        client.download_submissions_archive(
            tmp_path, previous_etag='"cached"', _sleep_fn=lambda _: None
        )

    _, call_kwargs = mock_http.stream.call_args
    assert call_kwargs["headers"].get("If-None-Match") == '"cached"'


def test_conditional_request_sends_if_modified_since(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    r = _resp(200, chunks=[zip_bytes])
    mock_http = _mock_http(r)
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=mock_http):
        client.download_submissions_archive(
            tmp_path,
            previous_last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
            _sleep_fn=lambda _: None,
        )

    _, call_kwargs = mock_http.stream.call_args
    assert "If-Modified-Since" in call_kwargs["headers"]


def test_no_tmp_file_after_successful_download(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    r = _resp(200, chunks=[zip_bytes])
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=_mock_http(r)):
        client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)

    assert not list(tmp_path.glob("submissions_tmp_*.zip"))


# ── Invalid archive ───────────────────────────────────────────────────────────


def test_non_zip_response_raises_invalid_archive(tmp_path):
    r = _resp(200, chunks=[b"this is not a zip"])
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=_mock_http(r)):
        with pytest.raises(SecInvalidArchiveError):
            client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)


def test_tmp_file_deleted_after_invalid_archive(tmp_path):
    r = _resp(200, chunks=[b"garbage"])
    client = _make_client()

    with patch("invest_ml.sec.client.httpx.Client", return_value=_mock_http(r)):
        with pytest.raises(SecInvalidArchiveError):
            client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)

    assert not list(tmp_path.glob("submissions_tmp_*.zip"))


# ── Permanent 4xx error ───────────────────────────────────────────────────────


def test_404_raises_immediately_no_retry(tmp_path):
    r = _resp(404)
    client = _make_client(max_retries=3)
    mock_http = _mock_http(r)

    with patch("invest_ml.sec.client.httpx.Client", return_value=mock_http):
        with pytest.raises(SecDownloadError, match="permanent"):
            client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)

    # Only one attempt — the stream method was called once.
    assert mock_http.stream.call_count == 1


# ── Transient errors with retry ───────────────────────────────────────────────


def test_429_retries_then_succeeds(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    sleep_calls: list[float] = []
    client = _make_client(max_retries=2)

    http_429 = _mock_http(_resp(429))
    http_200 = _mock_http(_resp(200, chunks=[zip_bytes]))

    with patch(
        "invest_ml.sec.client.httpx.Client", side_effect=[http_429, http_200]
    ):
        result = client.download_submissions_archive(
            tmp_path, _sleep_fn=sleep_calls.append
        )

    assert result.not_modified is False
    assert result.sha256 == hashlib.sha256(zip_bytes).hexdigest()
    assert len(sleep_calls) == 1


def test_503_retries_then_succeeds(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    client = _make_client(max_retries=2)

    http_503 = _mock_http(_resp(503))
    http_200 = _mock_http(_resp(200, chunks=[zip_bytes]))

    with patch(
        "invest_ml.sec.client.httpx.Client", side_effect=[http_503, http_200]
    ):
        result = client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)

    assert result.path.exists()


def test_retry_after_header_overrides_backoff(tmp_path):
    zip_bytes = _minimal_zip_bytes()
    sleep_calls: list[float] = []
    client = _make_client(max_retries=2)

    http_429 = _mock_http(_resp(429, headers={"retry-after": "30"}))
    http_200 = _mock_http(_resp(200, chunks=[zip_bytes]))

    with patch(
        "invest_ml.sec.client.httpx.Client", side_effect=[http_429, http_200]
    ):
        client.download_submissions_archive(tmp_path, _sleep_fn=sleep_calls.append)

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 30.0


def test_exceeds_max_retries_raises(tmp_path):
    client = _make_client(max_retries=1)

    http_429_a = _mock_http(_resp(429))
    http_429_b = _mock_http(_resp(429))

    with patch(
        "invest_ml.sec.client.httpx.Client", side_effect=[http_429_a, http_429_b]
    ):
        with pytest.raises(SecDownloadError):
            client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)


def test_transport_error_retries_then_raises(tmp_path):
    client = _make_client(max_retries=1)

    def _raise_transport(*args, **kwargs):
        raise httpx.TransportError("connection refused")

    stream_ctx_err = MagicMock()
    stream_ctx_err.__enter__ = MagicMock(side_effect=_raise_transport)
    stream_ctx_err.__exit__ = MagicMock(return_value=False)

    http_err = MagicMock()
    http_err.__enter__ = MagicMock(return_value=http_err)
    http_err.__exit__ = MagicMock(return_value=False)
    http_err.stream.return_value = stream_ctx_err

    with patch(
        "invest_ml.sec.client.httpx.Client", side_effect=[http_err, http_err]
    ):
        with pytest.raises(SecDownloadError, match="Transport error"):
            client.download_submissions_archive(tmp_path, _sleep_fn=lambda _: None)
