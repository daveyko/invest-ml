"""Unit tests for CompanyDataProfileRepository.

Uses mocked SQLAlchemy sessions — no real database required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from invest_ml.db.repositories.company_data_profiles import (
    CompanyDataProfileRepository,
    CompanyProfileTarget,
    ProfileUpsertResult,
)
from invest_ml.sec.profiler import CompanyDataProfileResult

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_session() -> MagicMock:
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


def _make_profile(company_id=None, profile_version="test_v1") -> CompanyDataProfileResult:
    return CompanyDataProfileResult(
        company_id=company_id or uuid4(),
        profile_version=profile_version,
        scanned_at=_NOW,
        source_run_id=uuid4(),
        first_period_end=None,
        latest_period_end=None,
        latest_filed_date=None,
        annual_periods=0,
        quarterly_periods=0,
        has_revenue=False,
        has_operating_income=False,
        has_net_income=False,
        has_operating_cash_flow=False,
        has_cash=False,
        has_debt=False,
        has_shares=False,
        canonical_metric_coverage=0.0,
        fact_count=0,
        quality_flags={},
    )


# ── Ingestion run methods ─────────────────────────────────────────────────────


def test_create_ingestion_run_adds_to_session():
    session = _make_session()
    repo = CompanyDataProfileRepository(session)
    run = repo.create_ingestion_run(
        source="sec_companyfacts_bulk_profile",
        source_uri="https://example.com/cf.zip",
        started_at=_NOW,
    )
    session.add.assert_called_once()
    session.flush.assert_called_once()


def test_fail_ingestion_run_calls_execute():
    session = _make_session()
    repo = CompanyDataProfileRepository(session)
    repo.fail_ingestion_run(uuid4(), error="boom")
    session.execute.assert_called_once()


def test_succeed_ingestion_run_calls_execute():
    session = _make_session()
    repo = CompanyDataProfileRepository(session)
    repo.succeed_ingestion_run(uuid4(), archive_hash="abc", entities_checked=5)
    session.execute.assert_called_once()


# ── list_companyfacts_profile_targets ────────────────────────────────────────


def test_list_targets_returns_profile_target_list():
    """Verify the method returns correctly typed objects from query rows."""
    session = _make_session()
    company_id = uuid4()
    cik = "0000723125"

    fake_row = MagicMock()
    fake_row.__getitem__ = lambda self, i: company_id if i == 0 else cik
    session.execute.return_value.all.return_value = [(company_id, cik)]

    repo = CompanyDataProfileRepository(session)
    targets = repo.list_companyfacts_profile_targets(
        exchanges=["Nasdaq"],
        entity_types=["operating"],
    )

    assert len(targets) == 1
    assert isinstance(targets[0], CompanyProfileTarget)
    assert targets[0].cik == cik
    assert targets[0].company_id == company_id


def test_list_targets_empty_result():
    session = _make_session()
    session.execute.return_value.all.return_value = []
    repo = CompanyDataProfileRepository(session)
    targets = repo.list_companyfacts_profile_targets(
        exchanges=["Nasdaq"],
        entity_types=["operating"],
    )
    assert targets == []


# ── upsert_profiles ───────────────────────────────────────────────────────────


def test_upsert_empty_list_returns_zero():
    session = _make_session()
    repo = CompanyDataProfileRepository(session)
    result = repo.upsert_profiles([])
    assert result.upserted == 0
    session.execute.assert_not_called()


def test_upsert_single_profile_executes_once():
    session = _make_session()
    repo = CompanyDataProfileRepository(session)
    result = repo.upsert_profiles([_make_profile()])
    assert result.upserted == 1
    session.execute.assert_called_once()


def test_upsert_multiple_profiles_executes_for_each():
    session = _make_session()
    repo = CompanyDataProfileRepository(session)
    profiles = [_make_profile() for _ in range(5)]
    result = repo.upsert_profiles(profiles)
    assert result.upserted == 5
    assert session.execute.call_count == 5


def test_upsert_result_type():
    session = _make_session()
    repo = CompanyDataProfileRepository(session)
    result = repo.upsert_profiles([_make_profile()])
    assert isinstance(result, ProfileUpsertResult)
