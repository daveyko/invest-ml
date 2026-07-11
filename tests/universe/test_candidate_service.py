"""Unit tests for CandidateUniverseService.

Uses mocked UniverseRepository to test orchestration logic without a real DB.
"""

from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from invest_ml.universe.config import CandidateUniverseConfig
from invest_ml.universe.models import (
    CandidateCompanyInput,
    CandidateSecurity,
    CandidateUniverseResult,
)
from invest_ml.universe.service import CandidateUniverseService

# ── Helpers ───────────────────────────────────────────────────────────────────

AS_OF = date(2026, 1, 11)
_REPO_PATH = "invest_ml.db.repositories.universe.UniverseRepository"


def _config(**overrides) -> CandidateUniverseConfig:
    defaults = dict(
        name="candidate",
        version="v1",
        supported_exchanges=("Nasdaq",),
        exchange_aliases={"Nasdaq": "Nasdaq"},
        allowed_entity_types=("operating",),
        excluded_exact_entity_types=(),
        exclude_missing_entity_type=True,
        require_current_ticker=True,
        require_company_data_profile=False,
        recent_filing_months=18,
        exclude_missing_recent_filing=True,
        excluded_sic_codes=frozenset(),
        excluded_name_patterns=(),
        always_exclude_ciks=frozenset(),
        always_include_ciks=frozenset(),
        profile_version="companyfacts_profile_v1",
    )
    defaults.update(overrides)
    return CandidateUniverseConfig(**defaults)


def _sec(ticker="ACME", exchange="Nasdaq") -> CandidateSecurity:
    return CandidateSecurity(
        security_id=uuid4(),
        ticker=ticker,
        exchange=exchange,
        normalized_exchange="Nasdaq",
        currently_observed=True,
    )


def _company(
    *,
    cik="0000111111",
    entity_type="operating",
    filing_date=date(2025, 12, 1),
    has_profile=True,
) -> CandidateCompanyInput:
    return CandidateCompanyInput(
        company_id=uuid4(),
        cik=cik,
        legal_name="Test Corp",
        entity_type=entity_type,
        latest_filing_date=filing_date,
        sic_codes=("7372",),
        has_current_data_profile=has_profile,
        securities=(_sec(),),
    )


def _mock_universe_def(name="candidate", version="v1", criteria_hash=None) -> MagicMock:
    defn = MagicMock()
    defn.universe_id = uuid4()
    defn.name = name
    defn.version = version
    defn.criteria = {"criteria_hash": criteria_hash}
    return defn


def _make_service() -> tuple[CandidateUniverseService, MagicMock]:
    """Return (service, mock_session_factory)."""
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    factory = MagicMock(return_value=mock_session)
    service = CandidateUniverseService(session_factory=factory)
    return service, mock_session


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_creates_universe_definition_when_not_exists():
    """On first run find returns None → create_universe_definition is called."""
    service, _ = _make_service()
    cfg = _config()
    expected_hash = cfg.criteria_hash()

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.find_universe_definition.return_value = None
        defn = _mock_universe_def(criteria_hash=expected_hash)
        inst.create_universe_definition.return_value = defn
        inst.list_candidate_inputs.return_value = []
        inst.list_active_memberships.return_value = []

        service.materialize(
            as_of_date=AS_OF,
            universe_name="candidate",
            universe_version="v1",
            profile_version="companyfacts_profile_v1",
            config=cfg,
        )

    inst.find_universe_definition.assert_called_once_with("candidate", "v1")
    inst.create_universe_definition.assert_called_once()
    call_kwargs = inst.create_universe_definition.call_args
    assert call_kwargs.kwargs["purpose"] == "candidate"
    assert call_kwargs.kwargs["criteria"]["criteria_hash"] == expected_hash


def test_validates_existing_universe_same_hash():
    """If universe exists with matching hash, no new definition is created."""
    service, _ = _make_service()
    cfg = _config()
    expected_hash = cfg.criteria_hash()

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.find_universe_definition.return_value = _mock_universe_def(
            criteria_hash=expected_hash
        )
        inst.list_candidate_inputs.return_value = []
        inst.list_active_memberships.return_value = []

        result = service.materialize(
            as_of_date=AS_OF,
            universe_name="candidate",
            universe_version="v1",
            profile_version="companyfacts_profile_v1",
            config=cfg,
        )

    inst.create_universe_definition.assert_not_called()
    assert isinstance(result, CandidateUniverseResult)


def test_raises_on_criteria_hash_mismatch():
    """If universe exists with a DIFFERENT hash, ValueError is raised."""
    service, _ = _make_service()
    cfg = _config()

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.find_universe_definition.return_value = _mock_universe_def(
            criteria_hash="old_hash_value"
        )

        with pytest.raises(ValueError, match="criteria_hash"):
            service.materialize(
                as_of_date=AS_OF,
                universe_name="candidate",
                universe_version="v1",
                profile_version="companyfacts_profile_v1",
                config=cfg,
            )


def test_newly_included_companies_inserted():
    """Companies passing evaluation with no prior active membership → insert_membership."""
    service, _ = _make_service()
    cfg = _config()
    company_a = _company(cik="0000111111")
    company_b = _company(cik="0000222222")

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.find_universe_definition.return_value = _mock_universe_def(
            criteria_hash=cfg.criteria_hash()
        )
        inst.list_candidate_inputs.return_value = [company_a, company_b]
        inst.list_active_memberships.return_value = []  # no prior members

        result = service.materialize(
            as_of_date=AS_OF,
            universe_name="candidate",
            universe_version="v1",
            profile_version="companyfacts_profile_v1",
            config=cfg,
        )

    assert inst.insert_membership.call_count == 2
    assert result.newly_included == 2
    assert result.already_included == 0
    assert result.newly_excluded == 0
    assert result.included_companies == 2


def test_already_included_companies_not_reinserted():
    """Company already in active membership → no insert_membership, no close_membership."""
    service, _ = _make_service()
    cfg = _config()
    company = _company(cik="0000111111")

    existing_membership = MagicMock()
    existing_membership.company_id = company.company_id

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.find_universe_definition.return_value = _mock_universe_def(
            criteria_hash=cfg.criteria_hash()
        )
        inst.list_candidate_inputs.return_value = [company]
        inst.list_active_memberships.return_value = [existing_membership]

        result = service.materialize(
            as_of_date=AS_OF,
            universe_name="candidate",
            universe_version="v1",
            profile_version="companyfacts_profile_v1",
            config=cfg,
        )

    inst.insert_membership.assert_not_called()
    inst.close_membership.assert_not_called()
    assert result.newly_included == 0
    assert result.already_included == 1
    assert result.newly_excluded == 0


def test_newly_excluded_company_closed():
    """Company active but now failing evaluation → close_membership called."""
    service, _ = _make_service()
    cfg = _config()
    stale_id = uuid4()
    # Company is in the DB (list_candidate_inputs) but fails evaluation (stale filing)
    company = CandidateCompanyInput(
        company_id=stale_id,
        cik="0000333333",
        legal_name="Old Corp",
        entity_type="operating",
        latest_filing_date=date(2020, 1, 1),  # stale
        sic_codes=(),
        has_current_data_profile=True,
        securities=(
            CandidateSecurity(
                security_id=uuid4(),
                ticker="OLD",
                exchange="Nasdaq",
                normalized_exchange="Nasdaq",
                currently_observed=True,
            ),
        ),
    )

    existing_membership = MagicMock()
    existing_membership.company_id = stale_id

    with patch(_REPO_PATH) as MockRepo:
        inst = MockRepo.return_value
        inst.find_universe_definition.return_value = _mock_universe_def(
            criteria_hash=cfg.criteria_hash()
        )
        inst.list_candidate_inputs.return_value = [company]
        inst.list_active_memberships.return_value = [existing_membership]

        result = service.materialize(
            as_of_date=AS_OF,
            universe_name="candidate",
            universe_version="v1",
            profile_version="companyfacts_profile_v1",
            config=cfg,
        )

    inst.close_membership.assert_called_once()
    close_kwargs = inst.close_membership.call_args.kwargs
    assert close_kwargs["company_id"] == stale_id
    assert close_kwargs["included_until"] == AS_OF
    assert "stale_latest_filing" in close_kwargs["exclusion_reasons"]["reason_codes"]

    assert result.newly_excluded == 1
    assert result.included_companies == 0
