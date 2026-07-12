"""Persistence tests for TrainingUniverseService and ScoringUniverseService.

All tests use mocked repository calls — no live database.
The repository is imported lazily inside service methods, so we patch it at its
definition site: invest_ml.db.repositories.universe.UniverseRepository.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from invest_ml.universe.scoring import (
    ScoringCompanyInput,
    ScoringUniverseConfig,
    SicBucketConfig,
)
from invest_ml.universe.training import (
    TrainingCompanyInput,
    TrainingUniverseConfig,
)

_AS_OF = date(2025, 6, 1)
_REPO_PATH = "invest_ml.db.repositories.universe.UniverseRepository"

_TRAINING_CFG = TrainingUniverseConfig.from_dict(
    {
        "name": "training_universe",
        "version": "v1",
        "candidate_universe": {"name": "candidate", "version": "v1"},
        "company_data_profile_version": "companyfacts_profile_v1",
        "market_profile_version": "market_profile_v1",
        "minimum_annual_periods": 3,
        "minimum_quarterly_periods": 0,
        "minimum_canonical_metric_coverage": 0.80,
        "minimum_price_history_years": 3,
        "minimum_median_daily_dollar_volume": 2_000_000,
        "maximum_missing_trading_day_ratio": 0.02,
        "maximum_market_profile_age_days": 45,
        "require_market_profile_status": "success",
        "require_latest_adjusted_close": True,
        "minimum_market_cap": None,
    }
)

_SCORING_CFG = ScoringUniverseConfig.from_dict(
    {
        "name": "scoring_universe",
        "version": "v1",
        "training_universe": {"name": "training_universe", "version": "v1"},
        "included_model_buckets": ["semiconductors"],
        "manual_include_ciks": [],
        "manual_include_tickers": [],
        "manual_exclude_ciks": [],
        "manual_exclude_tickers": [],
    }
)

_SIC_BUCKETS = SicBucketConfig.from_dict(
    {"model_buckets": {"semiconductors": {"sic_codes": ["3674"]}}}
)


def _mock_session(repo):
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


# ── TrainingUniverseService ───────────────────────────────────────────────────


def test_training_service_creates_universe_definition_when_absent():
    from invest_ml.universe.service import TrainingUniverseService

    repo = MagicMock()
    universe_id = uuid4()
    new_def = MagicMock(
        universe_id=universe_id,
        criteria={"criteria_hash": _TRAINING_CFG.criteria_hash()},
    )
    repo.find_universe_definition.return_value = None
    repo.create_universe_definition.return_value = new_def
    repo.list_active_memberships.return_value = []
    repo.load_training_company_inputs.return_value = []

    session = _mock_session(repo)
    with patch(_REPO_PATH, return_value=repo):
        svc = TrainingUniverseService(session_factory=MagicMock(return_value=session))
        result = svc.materialize(as_of_date=_AS_OF, config=_TRAINING_CFG)

    repo.create_universe_definition.assert_called_once()
    assert result.evaluated_companies == 0


def test_training_service_raises_on_hash_mismatch():
    from invest_ml.universe.service import TrainingUniverseService

    repo = MagicMock()
    existing_def = MagicMock(
        universe_id=uuid4(),
        criteria={"criteria_hash": "stale_hash"},
    )
    repo.find_universe_definition.return_value = existing_def

    session = _mock_session(repo)
    with patch(_REPO_PATH, return_value=repo):
        svc = TrainingUniverseService(session_factory=MagicMock(return_value=session))
        with pytest.raises(ValueError, match="criteria_hash"):
            svc.materialize(as_of_date=_AS_OF, config=_TRAINING_CFG)


def test_training_service_inserts_newly_included():
    from invest_ml.universe.security_selector import EligibleSecurityInput
    from invest_ml.universe.service import TrainingUniverseService

    sec_id = uuid4()
    company_id = uuid4()
    universe_id = uuid4()

    sec = EligibleSecurityInput(
        security_id=sec_id,
        company_id=company_id,
        ticker="CHIP",
        exchange="Nasdaq",
        currently_observed=True,
        market_profile_version="market_profile_v1",
        market_profile_scanned_at=datetime(2025, 5, 31, tzinfo=UTC),
        market_profile_status="success",
        first_price_date=date(2020, 1, 1),
        latest_price_date=date(2025, 5, 31),
        price_history_years=Decimal("5"),
        median_daily_dollar_volume=Decimal("5_000_000"),
        current_market_cap=Decimal("1_000_000_000"),
        missing_trading_day_ratio=Decimal("0.005"),
        latest_adjusted_close=Decimal("200.00"),
    )
    company = TrainingCompanyInput(
        company_id=company_id,
        cik="0001234567",
        legal_name="Chipco",
        candidate_membership_active=True,
        company_data_profile_version="companyfacts_profile_v1",
        annual_periods=5,
        quarterly_periods=20,
        canonical_metric_coverage=Decimal("0.90"),
        company_data_quality_flags={},
        securities=(sec,),
    )

    repo = MagicMock()
    existing_def = MagicMock(
        universe_id=universe_id,
        criteria={"criteria_hash": _TRAINING_CFG.criteria_hash()},
    )
    repo.find_universe_definition.return_value = existing_def
    repo.list_active_memberships.return_value = []
    repo.load_training_company_inputs.return_value = [company]

    session = _mock_session(repo)
    with patch(_REPO_PATH, return_value=repo):
        svc = TrainingUniverseService(session_factory=MagicMock(return_value=session))
        result = svc.materialize(as_of_date=_AS_OF, config=_TRAINING_CFG)

    repo.insert_membership.assert_called_once()
    call_kwargs = repo.insert_membership.call_args.kwargs
    assert call_kwargs["company_id"] == company_id
    assert call_kwargs["security_id"] == sec_id
    assert result.newly_included == 1
    assert result.included_companies == 1


def test_training_service_closes_excluded_members():
    from invest_ml.universe.service import TrainingUniverseService

    company_id = uuid4()
    universe_id = uuid4()

    active_membership = MagicMock(
        company_id=company_id,
        security_id=uuid4(),
        inclusion_reasons={},
    )

    repo = MagicMock()
    existing_def = MagicMock(
        universe_id=universe_id,
        criteria={"criteria_hash": _TRAINING_CFG.criteria_hash()},
    )
    repo.find_universe_definition.return_value = existing_def
    repo.list_active_memberships.return_value = [active_membership]
    repo.load_training_company_inputs.return_value = []

    session = _mock_session(repo)
    with patch(_REPO_PATH, return_value=repo):
        svc = TrainingUniverseService(session_factory=MagicMock(return_value=session))
        result = svc.materialize(as_of_date=_AS_OF, config=_TRAINING_CFG)

    repo.close_membership.assert_called_once()
    assert result.newly_excluded == 1


# ── ScoringUniverseService ────────────────────────────────────────────────────


def test_scoring_service_creates_universe_definition_when_absent():
    from invest_ml.universe.service import ScoringUniverseService

    sic_hash = _SIC_BUCKETS.config_hash()
    repo = MagicMock()
    universe_id = uuid4()
    new_def = MagicMock(
        universe_id=universe_id,
        criteria={"criteria_hash": _SCORING_CFG.criteria_hash(sic_hash)},
    )
    repo.find_universe_definition.return_value = None
    repo.create_universe_definition.return_value = new_def
    repo.list_active_memberships.return_value = []
    repo.load_scoring_company_inputs.return_value = []

    session = _mock_session(repo)
    with patch(_REPO_PATH, return_value=repo):
        svc = ScoringUniverseService(session_factory=MagicMock(return_value=session))
        result = svc.materialize(
            as_of_date=_AS_OF, config=_SCORING_CFG, sic_buckets=_SIC_BUCKETS
        )

    repo.create_universe_definition.assert_called_once()
    assert result.evaluated_training_members == 0


def test_scoring_service_inserts_bucket_matched_member():
    from invest_ml.universe.service import ScoringUniverseService

    company_id = uuid4()
    security_id = uuid4()
    universe_id = uuid4()
    sic_hash = _SIC_BUCKETS.config_hash()

    member = ScoringCompanyInput(
        company_id=company_id,
        security_id=security_id,
        cik="0001234567",
        ticker="SEMI",
        legal_name="Semico",
        active_sic_codes=("3674",),
        training_inclusion_reasons={},
    )

    repo = MagicMock()
    existing_def = MagicMock(
        universe_id=universe_id,
        criteria={"criteria_hash": _SCORING_CFG.criteria_hash(sic_hash)},
    )
    repo.find_universe_definition.return_value = existing_def
    repo.list_active_memberships.return_value = []
    repo.load_scoring_company_inputs.return_value = [member]

    session = _mock_session(repo)
    with patch(_REPO_PATH, return_value=repo):
        svc = ScoringUniverseService(session_factory=MagicMock(return_value=session))
        result = svc.materialize(
            as_of_date=_AS_OF, config=_SCORING_CFG, sic_buckets=_SIC_BUCKETS
        )

    repo.insert_membership.assert_called_once()
    assert result.included_companies == 1
    assert result.bucket_inclusions == 1
    assert result.manual_inclusions == 0
