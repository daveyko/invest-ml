"""Unit tests for CompanyCatalogService.

The session factory and CompanyCatalogRepository are mocked so no database
connection is required.  The archive reader and parser are also mocked to
control what records the service sees.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from invest_ml.company_catalog.service import CompanyCatalogService
from invest_ml.sec.archive import SubmissionArchiveRecord
from invest_ml.sec.submissions import CatalogCompany, CatalogSecurity, ParseResult

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
_TODAY = _NOW.date()
_RUN_ID = uuid4()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_catalog_company(
    cik: str = "0000000001",
    name: str = "Test Corp",
    sic: str | None = "7372",
    tickers: list[tuple[str, str | None]] | None = None,
) -> CatalogCompany:
    if tickers is None:
        tickers = [("TSST", "Nasdaq")]
    return CatalogCompany(
        cik=cik,
        legal_name=name,
        entity_type="operating",
        sic=sic,
        sic_description="Prepackaged Software" if sic == "7372" else None,
        fiscal_year_end="1231",
        state_of_incorporation="DE",
        filer_category="Large accelerated filer",
        latest_filing_date=date(2025, 12, 31),
        securities=tuple(CatalogSecurity(ticker=t, exchange=e) for t, e in tickers),
    )


def _parse_ok(company: CatalogCompany) -> ParseResult:
    return ParseResult(company=company, warnings=[])


def _parse_fail(error: str = "bad json") -> ParseResult:
    return ParseResult(company=None, warnings=[], error=error)


def _make_archive_record(cik: str = "0000000001") -> SubmissionArchiveRecord:
    payload = json.dumps({"cik": cik, "name": "stub"}).encode()
    return SubmissionArchiveRecord(member_name=f"CIK{cik}.json", payload=payload)


def _make_session_and_factory():
    """Return (mock_session, mock_session_factory)."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    factory = MagicMock(return_value=session)
    return session, factory


def _make_repo_mock(
    *,
    company_inserted: bool = True,
    security_inserted: bool = True,
    sic_inserted: bool = True,
) -> MagicMock:
    repo = MagicMock()
    company = MagicMock()
    company.company_id = uuid4()
    repo.upsert_company.return_value = (company, company_inserted)
    repo.upsert_security.return_value = (MagicMock(), security_inserted)
    repo.upsert_sec_sic_classification.return_value = sic_inserted
    return repo


# ── Test: first run inserts ───────────────────────────────────────────────────


def test_first_run_inserts_all_records():
    companies = [
        _make_catalog_company("0000000001", "Alpha Inc"),
        _make_catalog_company("0000000002", "Beta Corp"),
    ]
    records = [_make_archive_record(c.cik) for c in companies]

    mock_reader = MagicMock()
    mock_reader.iter_company_records.return_value = iter(records)

    mock_parser = MagicMock()
    mock_parser.parse.side_effect = [_parse_ok(c) for c in companies]

    session, factory = _make_session_and_factory()

    with patch(
        "invest_ml.company_catalog.service.CompanyCatalogRepository",
        return_value=_make_repo_mock(company_inserted=True),
    ):
        svc = CompanyCatalogService(factory, archive_reader=mock_reader, parser=mock_parser)
        result = svc.refresh_catalog(Path("fake.zip"), _RUN_ID, _NOW)

    assert result.companies_seen == 2
    assert result.companies_inserted == 2
    assert result.companies_updated == 0
    assert result.malformed_records == 0


# ── Test: second run is idempotent (updates, not inserts) ────────────────────


def test_second_run_updates_existing_companies():
    company = _make_catalog_company()
    record = _make_archive_record(company.cik)

    mock_reader = MagicMock()
    mock_reader.iter_company_records.return_value = iter([record])

    mock_parser = MagicMock()
    mock_parser.parse.return_value = _parse_ok(company)

    session, factory = _make_session_and_factory()

    with patch(
        "invest_ml.company_catalog.service.CompanyCatalogRepository",
        return_value=_make_repo_mock(company_inserted=False, security_inserted=False),
    ):
        svc = CompanyCatalogService(factory, archive_reader=mock_reader, parser=mock_parser)
        result = svc.refresh_catalog(Path("fake.zip"), _RUN_ID, _NOW)

    assert result.companies_seen == 1
    assert result.companies_inserted == 0
    assert result.companies_updated == 1
    assert result.securities_inserted == 0
    assert result.securities_updated == 1


# ── Test: malformed record is counted but does not discard valid ones ─────────


def test_malformed_record_does_not_abort_valid_records():
    good_company = _make_catalog_company("0000000002")
    records = [
        _make_archive_record("0000000001"),
        _make_archive_record("0000000002"),
        _make_archive_record("0000000003"),
    ]

    mock_reader = MagicMock()
    mock_reader.iter_company_records.return_value = iter(records)

    mock_parser = MagicMock()
    mock_parser.parse.side_effect = [
        _parse_fail("bad json in CIK1"),
        _parse_ok(good_company),
        _parse_fail("bad json in CIK3"),
    ]

    session, factory = _make_session_and_factory()

    with patch(
        "invest_ml.company_catalog.service.CompanyCatalogRepository",
        return_value=_make_repo_mock(),
    ):
        svc = CompanyCatalogService(factory, archive_reader=mock_reader, parser=mock_parser)
        result = svc.refresh_catalog(Path("fake.zip"), _RUN_ID, _NOW)

    assert result.malformed_records == 2
    assert result.companies_seen == 1
    assert result.companies_inserted == 1


# ── Test: SIC classification inserted on first encounter ─────────────────────


def test_new_sic_classification_is_counted():
    company = _make_catalog_company(sic="3674")
    record = _make_archive_record(company.cik)

    mock_reader = MagicMock()
    mock_reader.iter_company_records.return_value = iter([record])

    mock_parser = MagicMock()
    mock_parser.parse.return_value = _parse_ok(company)

    session, factory = _make_session_and_factory()

    with patch(
        "invest_ml.company_catalog.service.CompanyCatalogRepository",
        return_value=_make_repo_mock(sic_inserted=True),
    ):
        svc = CompanyCatalogService(factory, archive_reader=mock_reader, parser=mock_parser)
        result = svc.refresh_catalog(Path("fake.zip"), _RUN_ID, _NOW)

    assert result.sic_classifications_inserted == 1


# ── Test: SIC unchanged → no new classification row ──────────────────────────


def test_unchanged_sic_is_not_counted():
    company = _make_catalog_company(sic="3674")
    record = _make_archive_record(company.cik)

    mock_reader = MagicMock()
    mock_reader.iter_company_records.return_value = iter([record])

    mock_parser = MagicMock()
    mock_parser.parse.return_value = _parse_ok(company)

    session, factory = _make_session_and_factory()

    with patch(
        "invest_ml.company_catalog.service.CompanyCatalogRepository",
        return_value=_make_repo_mock(sic_inserted=False),
    ):
        svc = CompanyCatalogService(factory, archive_reader=mock_reader, parser=mock_parser)
        result = svc.refresh_catalog(Path("fake.zip"), _RUN_ID, _NOW)

    assert result.sic_classifications_inserted == 0


# ── Test: no SIC → no classification call ────────────────────────────────────


def test_company_without_sic_skips_classification_upsert():
    company = _make_catalog_company(sic=None)
    record = _make_archive_record(company.cik)

    mock_reader = MagicMock()
    mock_reader.iter_company_records.return_value = iter([record])

    mock_parser = MagicMock()
    mock_parser.parse.return_value = _parse_ok(company)

    session, factory = _make_session_and_factory()
    mock_repo = _make_repo_mock()

    with patch(
        "invest_ml.company_catalog.service.CompanyCatalogRepository",
        return_value=mock_repo,
    ):
        svc = CompanyCatalogService(factory, archive_reader=mock_reader, parser=mock_parser)
        svc.refresh_catalog(Path("fake.zip"), _RUN_ID, _NOW)

    mock_repo.upsert_sec_sic_classification.assert_not_called()


# ── Test: parse warnings are accumulated ─────────────────────────────────────


def test_parse_warnings_are_accumulated_in_result():
    company = _make_catalog_company()
    record = _make_archive_record(company.cik)

    mock_reader = MagicMock()
    mock_reader.iter_company_records.return_value = iter([record])

    mock_parser = MagicMock()
    mock_parser.parse.return_value = ParseResult(
        company=company, warnings=["length mismatch", "bad date"]
    )

    session, factory = _make_session_and_factory()

    with patch(
        "invest_ml.company_catalog.service.CompanyCatalogRepository",
        return_value=_make_repo_mock(),
    ):
        svc = CompanyCatalogService(factory, archive_reader=mock_reader, parser=mock_parser)
        result = svc.refresh_catalog(Path("fake.zip"), _RUN_ID, _NOW)

    assert "length mismatch" in result.parse_warnings
    assert "bad date" in result.parse_warnings


# ── Test: batch boundary respected ───────────────────────────────────────────


def test_batch_boundary_flushes_correctly():
    """With _BATCH_SIZE=2, three records must produce two separate DB commits."""
    import invest_ml.company_catalog.service as svc_module

    original_batch_size = svc_module._BATCH_SIZE
    svc_module._BATCH_SIZE = 2  # temporarily lower batch size for this test

    try:
        companies = [_make_catalog_company(str(i).zfill(10)) for i in range(1, 4)]
        records = [_make_archive_record(c.cik) for c in companies]

        mock_reader = MagicMock()
        mock_reader.iter_company_records.return_value = iter(records)

        mock_parser = MagicMock()
        mock_parser.parse.side_effect = [_parse_ok(c) for c in companies]

        session, factory = _make_session_and_factory()
        commit_calls: list[int] = []

        def _track_commit():
            commit_calls.append(1)

        session.commit.side_effect = _track_commit

        with patch(
            "invest_ml.company_catalog.service.CompanyCatalogRepository",
            return_value=_make_repo_mock(),
        ):
            svc = CompanyCatalogService(factory, archive_reader=mock_reader, parser=mock_parser)
            result = svc.refresh_catalog(Path("fake.zip"), _RUN_ID, _NOW)

        # Batch 1 (2 records) + batch 2 (1 record) = 2 session contexts = 2 commits.
        assert result.companies_seen == 3
        assert len(commit_calls) == 2
    finally:
        svc_module._BATCH_SIZE = original_batch_size
