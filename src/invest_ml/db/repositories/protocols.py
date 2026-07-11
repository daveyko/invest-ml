"""Domain repository protocols.

Each protocol defines the read/write surface for one bounded context.
No generic CRUD abstraction is used; method names carry domain semantics.

Point-in-time invariant: every read method that can look up historical data
must accept an as_of_date parameter and filter on available_at/filed_date
<= as_of_date.  Callers must never pass a future date for feature construction.
"""

from datetime import date
from typing import Protocol
from uuid import UUID

from invest_ml.db.models.company import Company, Security
from invest_ml.db.models.classification import CompanyClassification
from invest_ml.db.models.profiling import CompanyDataProfile, CompanyMarketProfile
from invest_ml.db.models.universe import UniverseDefinition, UniverseMembership
from invest_ml.db.models.financials import RawSourceVersion, XbrlFact, CanonicalMetric
from invest_ml.db.models.market import PriceBar
from invest_ml.db.models.features import FeatureDefinition, FeatureSetDefinition, FeatureSnapshot
from invest_ml.db.models.modeling import Label, TrainingDataset, ModelRun, Prediction


class CompanyCatalogRepository(Protocol):
    def upsert_company(self, company: Company) -> Company: ...
    def upsert_security(self, security: Security) -> Security: ...
    def get_company_by_cik(self, cik: str) -> Company | None: ...
    def list_active_companies(self) -> list[Company]: ...


class CompanyDataProfileRepository(Protocol):
    def upsert_profile(self, profile: CompanyDataProfile) -> CompanyDataProfile: ...
    def get_profile(
        self, company_id: UUID, profile_version: str
    ) -> CompanyDataProfile | None: ...
    def list_profiles_for_universe_selection(
        self,
        profile_version: str,
        min_annual_periods: int,
        min_coverage: float,
    ) -> list[CompanyDataProfile]: ...


class UniverseRepository(Protocol):
    def upsert_universe(self, universe: UniverseDefinition) -> UniverseDefinition: ...
    def add_memberships(self, memberships: list[UniverseMembership]) -> None: ...
    def get_universe(self, name: str, version: str) -> UniverseDefinition | None: ...
    def list_members_at(self, universe_id: UUID, as_of_date: date) -> list[UniverseMembership]: ...


class RawSourceRepository(Protocol):
    def upsert_version(self, version: RawSourceVersion) -> RawSourceVersion: ...
    def get_latest_for_entity(
        self, source: str, entity_key: str
    ) -> RawSourceVersion | None: ...


class XbrlFactRepository(Protocol):
    def bulk_insert_ignore(self, facts: list[XbrlFact]) -> int: ...
    def list_facts_as_of(
        self,
        company_id: UUID,
        as_of_date: date,
        taxonomy: str | None = None,
        tags: list[str] | None = None,
    ) -> list[XbrlFact]:
        """Return facts with filed_date <= as_of_date."""
        ...


class CanonicalMetricRepository(Protocol):
    def bulk_insert_ignore(self, metrics: list[CanonicalMetric]) -> int: ...
    def get_metric_as_of(
        self,
        company_id: UUID,
        metric_name: str,
        as_of_date: date,
        period_type: str | None = None,
    ) -> CanonicalMetric | None:
        """Return the most recent metric with available_at <= as_of_date."""
        ...


class PriceRepository(Protocol):
    def bulk_insert_ignore(self, bars: list[PriceBar]) -> int: ...
    def get_price_as_of(
        self, security_id: UUID, as_of_date: date, source: str | None = None
    ) -> PriceBar | None:
        """Return the most recent bar with trading_date <= as_of_date."""
        ...
    def get_range(
        self, security_id: UUID, start_date: date, end_date: date, source: str | None = None
    ) -> list[PriceBar]: ...


class FeatureSnapshotRepository(Protocol):
    def insert_snapshot(self, snapshot: FeatureSnapshot) -> FeatureSnapshot:
        """Insert a new immutable snapshot.  Never update existing rows."""
        ...
    def get_snapshot(
        self,
        company_id: UUID,
        security_id: UUID,
        as_of_date: date,
        feature_set_id: UUID,
    ) -> FeatureSnapshot | None:
        """Return the latest snapshot for the given key (highest created_at)."""
        ...
    def upsert_feature_definition(self, defn: FeatureDefinition) -> FeatureDefinition: ...
    def upsert_feature_set(self, defn: FeatureSetDefinition) -> FeatureSetDefinition: ...


class LabelRepository(Protocol):
    def insert_label(self, label: Label) -> Label:
        """Insert a realized label.  Labels are immutable once created."""
        ...
    def get_label(
        self,
        company_id: UUID,
        security_id: UUID,
        as_of_date: date,
        target_spec_id: UUID,
    ) -> Label | None: ...
    def list_matured_labels(
        self, target_spec_id: UUID, matured_by: date
    ) -> list[Label]: ...


class DatasetRepository(Protocol):
    def create_dataset(self, dataset: TrainingDataset) -> TrainingDataset: ...
    def get_dataset(self, name: str, version: str) -> TrainingDataset | None: ...


class ModelRepository(Protocol):
    def record_model_run(self, run: ModelRun) -> ModelRun: ...
    def get_promoted_model(self, model_name: str) -> ModelRun | None: ...
    def promote_model(self, model_id: UUID) -> None: ...


class PredictionRepository(Protocol):
    def bulk_insert_predictions(self, predictions: list[Prediction]) -> int: ...
    def list_latest_predictions(
        self, model_id: UUID, prediction_date: date
    ) -> list[Prediction]: ...
