from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from invest_ml.db.base import Base


class TargetSpec(Base):
    """Versioned definition of a prediction target.

    horizon_months: how far into the future the label looks.
    return_threshold: minimum adjusted return required for label=True.
    """

    __tablename__ = "target_specs"

    target_spec_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_months: Mapped[int] = mapped_column(Integer, nullable=False)
    return_threshold: Mapped[float] = mapped_column(Numeric, nullable=False)
    definition: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("horizon_months > 0", name="ck_target_specs_horizon_months"),
        UniqueConstraint("name", "version", name="uq_target_specs_name_version"),
    )


class Label(Base):
    """Immutable realized outcome for a company/security/as-of-date.

    Labels use FUTURE data (prices after as_of_date) by design.
    Feature construction must NEVER do the same.

    A label is created only after enough time has elapsed to observe the
    full horizon return (as_of_date + horizon_months <= today).
    """

    __tablename__ = "labels"

    label_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_labels_company_id"),
        nullable=False,
    )
    security_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("securities.security_id", name="fk_labels_security_id"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_spec_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("target_specs.target_spec_id", name="fk_labels_target_spec_id"),
        nullable=False,
    )
    start_trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_adjusted_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    end_adjusted_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    realized_return: Mapped[float] = mapped_column(Numeric, nullable=False)
    label: Mapped[bool] = mapped_column(Boolean, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "security_id",
            "as_of_date",
            "target_spec_id",
            name="uq_labels",
        ),
    )


class TrainingDataset(Base):
    """Logical immutable dataset definition.

    Parquet artifact is optional and regenerable; the source of truth is the
    training_dataset_rows table.  content_hash covers the sorted set of
    (feature_snapshot_id, label_id) pairs.
    """

    __tablename__ = "training_datasets"

    dataset_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    universe_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("universe_definitions.universe_id", name="fk_training_datasets_universe_id"),
        nullable=False,
    )
    feature_set_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "feature_set_definitions.feature_set_id",
            name="fk_training_datasets_feature_set_id",
        ),
        nullable=False,
    )
    target_spec_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("target_specs.target_spec_id", name="fk_training_datasets_target_spec_id"),
        nullable=False,
    )
    train_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    train_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    build_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_training_datasets_name_version"),
    )


class TrainingDatasetRow(Base):
    """Exact immutable mapping from a dataset to a feature snapshot and label.

    Each row points to an immutable feature_snapshot and an immutable label.
    Neither may be updated after the dataset is built.
    """

    __tablename__ = "training_dataset_rows"

    dataset_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training_datasets.dataset_id", name="fk_training_dataset_rows_dataset_id"),
        primary_key=True,
        nullable=False,
    )
    feature_snapshot_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "feature_snapshots.feature_snapshot_id",
            name="fk_training_dataset_rows_snapshot_id",
        ),
        primary_key=True,
        nullable=False,
    )
    label_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("labels.label_id", name="fk_training_dataset_rows_label_id"),
        nullable=False,
    )
    split: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "split IN ('train', 'validation', 'test')",
            name="ck_training_dataset_rows_split",
        ),
        UniqueConstraint(
            "dataset_id", "label_id", name="uq_training_dataset_rows_dataset_label"
        ),
    )


class ModelRun(Base):
    """Record of a trained model artifact.

    status lifecycle: candidate → promoted (serves predictions) or rejected.
    promoted models may later be retired.
    """

    __tablename__ = "model_runs"

    model_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_type: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("training_datasets.dataset_id", name="fk_model_runs_dataset_id"),
        nullable=False,
    )
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_hash: Mapped[str] = mapped_column(Text, nullable=False)
    hyperparameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    git_sha: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'promoted', 'rejected', 'retired')",
            name="ck_model_runs_status",
        ),
        UniqueConstraint("model_name", "model_version", name="uq_model_runs_name_version"),
    )


class Prediction(Base):
    """Output probability from a promoted model for a company/security."""

    __tablename__ = "predictions"

    prediction_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    model_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_runs.model_id", name="fk_predictions_model_id"),
        nullable=False,
    )
    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_predictions_company_id"),
        nullable=False,
    )
    security_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("securities.security_id", name="fk_predictions_security_id"),
        nullable=False,
    )
    feature_snapshot_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "feature_snapshots.feature_snapshot_id",
            name="fk_predictions_feature_snapshot_id",
        ),
        nullable=False,
    )
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    probability: Mapped[float] = mapped_column(Numeric, nullable=False)
    universe_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "probability >= 0 AND probability <= 1",
            name="ck_predictions_probability_range",
        ),
        UniqueConstraint(
            "model_id",
            "security_id",
            "prediction_date",
            name="uq_predictions_model_security_date",
        ),
    )
