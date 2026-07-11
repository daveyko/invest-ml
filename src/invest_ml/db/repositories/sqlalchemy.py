"""SQLAlchemy-backed repository implementations.

All methods raise NotImplementedError until implemented.  The class skeletons
and docstrings document the intended contract.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from invest_ml.db.models.company import Company, Security
from invest_ml.db.models.profiling import CompanyDataProfile, CompanyMarketProfile
from invest_ml.db.models.universe import UniverseDefinition, UniverseMembership
from invest_ml.db.models.financials import RawSourceVersion, XbrlFact, CanonicalMetric
from invest_ml.db.models.market import PriceBar
from invest_ml.db.models.features import FeatureDefinition, FeatureSetDefinition, FeatureSnapshot
from invest_ml.db.models.modeling import Label, TrainingDataset, ModelRun, Prediction


class SqlCompanyCatalogRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert_company(self, company: Company) -> Company:
        """Insert or update company by CIK."""
        raise NotImplementedError("TODO: implement CIK-based upsert")

    def upsert_security(self, security: Security) -> Security:
        """Insert or update security by (company_id, ticker, exchange)."""
        raise NotImplementedError("TODO: implement security upsert")

    def get_company_by_cik(self, cik: str) -> Company | None:
        raise NotImplementedError

    def list_active_companies(self) -> list[Company]:
        raise NotImplementedError


class SqlCompanyDataProfileRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert_profile(self, profile: CompanyDataProfile) -> CompanyDataProfile:
        """Insert or replace profile for (company_id, profile_version)."""
        raise NotImplementedError

    def get_profile(
        self, company_id: UUID, profile_version: str
    ) -> CompanyDataProfile | None:
        raise NotImplementedError

    def list_profiles_for_universe_selection(
        self,
        profile_version: str,
        min_annual_periods: int,
        min_coverage: float,
    ) -> list[CompanyDataProfile]:
        """Return profiles meeting minimum data-quality thresholds."""
        raise NotImplementedError


class SqlUniverseRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert_universe(self, universe: UniverseDefinition) -> UniverseDefinition:
        raise NotImplementedError

    def add_memberships(self, memberships: list[UniverseMembership]) -> None:
        raise NotImplementedError

    def get_universe(self, name: str, version: str) -> UniverseDefinition | None:
        raise NotImplementedError

    def list_members_at(self, universe_id: UUID, as_of_date: date) -> list[UniverseMembership]:
        """Return memberships active on as_of_date (included_from <= as_of_date
        and included_until is null or > as_of_date)."""
        raise NotImplementedError


class SqlRawSourceRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert_version(self, version: RawSourceVersion) -> RawSourceVersion:
        raise NotImplementedError

    def get_latest_for_entity(
        self, source: str, entity_key: str
    ) -> RawSourceVersion | None:
        raise NotImplementedError


class SqlXbrlFactRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def bulk_insert_ignore(self, facts: list[XbrlFact]) -> int:
        """Insert facts, skipping duplicates on fact_id.  Returns inserted count."""
        raise NotImplementedError

    def list_facts_as_of(
        self,
        company_id: UUID,
        as_of_date: date,
        taxonomy: str | None = None,
        tags: list[str] | None = None,
    ) -> list[XbrlFact]:
        """filed_date <= as_of_date filter is mandatory for point-in-time correctness."""
        raise NotImplementedError


class SqlCanonicalMetricRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def bulk_insert_ignore(self, metrics: list[CanonicalMetric]) -> int:
        raise NotImplementedError

    def get_metric_as_of(
        self,
        company_id: UUID,
        metric_name: str,
        as_of_date: date,
        period_type: str | None = None,
    ) -> CanonicalMetric | None:
        """available_at <= as_of_date filter is mandatory."""
        raise NotImplementedError


class SqlPriceRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def bulk_insert_ignore(self, bars: list[PriceBar]) -> int:
        raise NotImplementedError

    def get_price_as_of(
        self, security_id: UUID, as_of_date: date, source: str | None = None
    ) -> PriceBar | None:
        raise NotImplementedError

    def get_range(
        self, security_id: UUID, start_date: date, end_date: date, source: str | None = None
    ) -> list[PriceBar]:
        raise NotImplementedError


class SqlFeatureSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def insert_snapshot(self, snapshot: FeatureSnapshot) -> FeatureSnapshot:
        """Insert snapshot.  Raises IntegrityError if source_lineage_hash already exists
        for the same (company, security, as_of_date, feature_set)."""
        raise NotImplementedError

    def get_snapshot(
        self,
        company_id: UUID,
        security_id: UUID,
        as_of_date: date,
        feature_set_id: UUID,
    ) -> FeatureSnapshot | None:
        raise NotImplementedError

    def upsert_feature_definition(self, defn: FeatureDefinition) -> FeatureDefinition:
        raise NotImplementedError

    def upsert_feature_set(self, defn: FeatureSetDefinition) -> FeatureSetDefinition:
        raise NotImplementedError


class SqlLabelRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def insert_label(self, label: Label) -> Label:
        raise NotImplementedError

    def get_label(
        self,
        company_id: UUID,
        security_id: UUID,
        as_of_date: date,
        target_spec_id: UUID,
    ) -> Label | None:
        raise NotImplementedError

    def list_matured_labels(
        self, target_spec_id: UUID, matured_by: date
    ) -> list[Label]:
        """Labels where end_trading_date <= matured_by."""
        raise NotImplementedError


class SqlDatasetRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create_dataset(self, dataset: TrainingDataset) -> TrainingDataset:
        raise NotImplementedError

    def get_dataset(self, name: str, version: str) -> TrainingDataset | None:
        raise NotImplementedError


class SqlModelRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def record_model_run(self, run: ModelRun) -> ModelRun:
        raise NotImplementedError

    def get_promoted_model(self, model_name: str) -> ModelRun | None:
        raise NotImplementedError

    def promote_model(self, model_id: UUID) -> None:
        raise NotImplementedError


class SqlPredictionRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def bulk_insert_predictions(self, predictions: list[Prediction]) -> int:
        raise NotImplementedError

    def list_latest_predictions(
        self, model_id: UUID, prediction_date: date
    ) -> list[Prediction]:
        raise NotImplementedError
