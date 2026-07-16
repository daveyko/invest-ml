"""Unit tests for TrainingUniversePartitionService.

All DB calls are mocked — no live database required.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from invest_ml.config.loaders import load_training_universe_config
from invest_ml.universe.security_selector import EligibleSecurityInput
from invest_ml.universe.training import (
    TrainingCompanyInput,
    TrainingUniversePartitionConfig,
    TrainingUniversePartitionResult,
)

_REPO_PATH = "invest_ml.db.repositories.universe.UniverseRepository"


def _load_config() -> TrainingUniversePartitionConfig:
    raw = load_training_universe_config()
    return TrainingUniversePartitionConfig.from_dict(raw)


def _session_factory():
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=mock_session)


def _make_universe_def(as_of_date: date | None = None):
    ud = MagicMock()
    ud.universe_id = uuid4()
    ud.as_of_date = as_of_date
    return ud


# ── Config loading ────────────────────────────────────────────────────────────


def test_config_loads_from_yaml():
    cfg = _load_config()
    assert cfg.name == "training"
    assert cfg.version == "training_v1"
    assert cfg.candidate_universe_name == "candidate"
    assert cfg.normalization_version == "canonical_metrics_v1"
    assert cfg.market_profile_version == "point_in_time_v1"
    assert cfg.minimum_annual_periods == 3
    assert cfg.liquidity_lookback_sessions == 90
    assert cfg.missing_ratio_lookback_years == 3
    assert cfg.partition_start_date == "2015-01-01"


def test_config_criteria_hash_is_stable():
    cfg = _load_config()
    h1 = cfg.criteria_hash()
    h2 = cfg.criteria_hash()
    assert h1 == h2
    assert len(h1) == 64


def test_config_to_training_universe_config():
    cfg = _load_config()
    tu_cfg = cfg.to_training_universe_config()
    assert tu_cfg.minimum_annual_periods == cfg.minimum_annual_periods
    assert tu_cfg.market_profile_version == cfg.market_profile_version
    assert tu_cfg.company_data_profile_version == cfg.normalization_version
    assert tu_cfg.maximum_market_profile_age_days == 9999


# ── Service: idempotency ──────────────────────────────────────────────────────


def test_already_present_partition_returns_immediately():
    """If the partition definition exists, return current member count without re-evaluating."""
    from invest_ml.universe.service import TrainingUniversePartitionService

    cfg = _load_config()
    as_of_date = date(2024, 1, 31)
    existing_uid = uuid4()

    with patch(_REPO_PATH) as MockRepo:
        repo_inst = MockRepo.return_value
        ud = MagicMock()
        ud.universe_id = existing_uid
        repo_inst.find_universe_definition.return_value = ud
        repo_inst.count_active_memberships.return_value = 42

        sf = _session_factory()
        service = TrainingUniversePartitionService(session_factory=sf)
        result = service.materialize(
            as_of_date=as_of_date,
            config=cfg,
            total_canonical_metrics=80,
        )

    assert isinstance(result, TrainingUniversePartitionResult)
    assert result.already_present is True
    assert result.included_companies == 42
    assert result.evaluated_companies == 0
    assert result.as_of_date == as_of_date
    # Should not call load_training_company_inputs_point_in_time
    repo_inst.load_training_company_inputs_point_in_time.assert_not_called()


# ── Service: first-run ────────────────────────────────────────────────────────


def test_first_run_creates_definition_and_memberships():
    """First materialization creates the universe definition and all inclusion rows."""
    from invest_ml.universe.service import TrainingUniversePartitionService

    cfg = _load_config()
    as_of_date = date(2024, 1, 31)
    company_id_a = uuid4()
    security_id_a = uuid4()
    universe_id = uuid4()

    scanned = datetime(2024, 1, 31, tzinfo=UTC)
    eligible_sec = EligibleSecurityInput(
        security_id=security_id_a,
        company_id=company_id_a,
        ticker="ACME",
        exchange="NYSE",
        currently_observed=True,
        market_profile_version="point_in_time_v1",
        market_profile_scanned_at=scanned,
        market_profile_status="success",
        first_price_date=date(2020, 1, 2),
        latest_price_date=date(2024, 1, 31),
        price_history_years=Decimal("4.0"),
        median_daily_dollar_volume=Decimal("5000000"),
        current_market_cap=None,
        missing_trading_day_ratio=Decimal("0.01"),
        latest_adjusted_close=Decimal("100.0"),
    )
    company_input = TrainingCompanyInput(
        company_id=company_id_a,
        cik="0001234567",
        legal_name="Acme Corp",
        candidate_membership_active=True,
        company_data_profile_version="canonical_metrics_v1",
        annual_periods=5,
        quarterly_periods=20,
        canonical_metric_coverage=Decimal("0.90"),
        company_data_quality_flags={},
        securities=(eligible_sec,),
    )

    with patch(_REPO_PATH) as MockRepo:
        repo_inst = MockRepo.return_value
        # Partition does not exist yet
        repo_inst.find_universe_definition.return_value = None
        # Point-in-time inputs
        repo_inst.load_training_company_inputs_point_in_time.return_value = [company_input]
        # Create definition
        ud = MagicMock()
        ud.universe_id = universe_id
        repo_inst.create_universe_definition.return_value = ud

        sf = _session_factory()
        service = TrainingUniversePartitionService(session_factory=sf)
        result = service.materialize(
            as_of_date=as_of_date,
            config=cfg,
            total_canonical_metrics=80,
        )

    assert result.already_present is False
    assert result.evaluated_companies == 1
    assert result.included_companies == 1
    assert result.newly_included == 1
    assert result.universe_id == universe_id
    # Membership must be inserted
    repo_inst.insert_membership.assert_called_once()
    call_kwargs = repo_inst.insert_membership.call_args.kwargs
    assert call_kwargs["universe_id"] == universe_id
    assert call_kwargs["company_id"] == company_id_a
    assert call_kwargs["security_id"] == security_id_a
    assert call_kwargs["included_from"] == as_of_date


def test_excluded_company_not_inserted():
    """Companies that fail eligibility must not get a membership row."""
    from invest_ml.universe.service import TrainingUniversePartitionService

    cfg = _load_config()
    as_of_date = date(2024, 1, 31)
    company_id_b = uuid4()
    universe_id = uuid4()

    # No annual periods → excluded via missing_company_data_profile
    company_input = TrainingCompanyInput(
        company_id=company_id_b,
        cik="0009999999",
        legal_name="No Data Corp",
        candidate_membership_active=True,
        company_data_profile_version=None,  # triggers exclusion
        annual_periods=0,
        quarterly_periods=0,
        canonical_metric_coverage=Decimal("0.0"),
        company_data_quality_flags={},
        securities=(),
    )

    with patch(_REPO_PATH) as MockRepo:
        repo_inst = MockRepo.return_value
        repo_inst.find_universe_definition.return_value = None
        repo_inst.load_training_company_inputs_point_in_time.return_value = [company_input]
        ud = MagicMock()
        ud.universe_id = universe_id
        repo_inst.create_universe_definition.return_value = ud

        sf = _session_factory()
        service = TrainingUniversePartitionService(session_factory=sf)
        result = service.materialize(
            as_of_date=as_of_date,
            config=cfg,
            total_canonical_metrics=80,
        )

    assert result.included_companies == 0
    assert result.evaluated_companies == 1
    repo_inst.insert_membership.assert_not_called()
    assert "missing_company_data_profile" in result.exclusion_counts


def test_universe_definition_created_with_as_of_date():
    """The created universe definition must carry the partition as_of_date."""
    from invest_ml.universe.service import TrainingUniversePartitionService

    cfg = _load_config()
    as_of_date = date(2024, 6, 30)

    with patch(_REPO_PATH) as MockRepo:
        repo_inst = MockRepo.return_value
        repo_inst.find_universe_definition.return_value = None
        repo_inst.load_training_company_inputs_point_in_time.return_value = []
        ud = MagicMock()
        ud.universe_id = uuid4()
        repo_inst.create_universe_definition.return_value = ud

        sf = _session_factory()
        service = TrainingUniversePartitionService(session_factory=sf)
        service.materialize(
            as_of_date=as_of_date,
            config=cfg,
            total_canonical_metrics=80,
        )

    create_kwargs = repo_inst.create_universe_definition.call_args.kwargs
    assert create_kwargs["name"] == "training"
    assert create_kwargs["version"] == "training_v1"
    assert create_kwargs["as_of_date"] == as_of_date
    assert create_kwargs["purpose"] == "training"
    assert "criteria_hash" in create_kwargs["criteria"]
    assert create_kwargs["criteria"]["as_of_date"] == as_of_date.isoformat()


# ── Config round-trip ─────────────────────────────────────────────────────────


def test_partition_start_date_is_valid_iso():
    cfg = _load_config()
    parsed = date.fromisoformat(cfg.partition_start_date)
    assert parsed.year >= 2010


def test_minimum_market_cap_is_none():
    cfg = _load_config()
    assert cfg.minimum_market_cap is None


def test_require_latest_adjusted_close_is_true():
    cfg = _load_config()
    assert cfg.require_latest_adjusted_close is True
