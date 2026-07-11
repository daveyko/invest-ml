from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from invest_ml.db.base import Base


class FeatureDefinition(Base):
    """Versioned specification of a single feature formula.

    A new row must be inserted whenever the formula, inputs, or lookback
    changes.  The feature_version tag (e.g. 'v1', 'v2') is the stability
    contract: the same version must always produce the same value given the
    same inputs.

    definition JSON example:
        {"lookback_months": 6, "input": "adjusted_close", "formula": "current / prior - 1"}
    """

    __tablename__ = "feature_definitions"

    feature_definition_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    feature_name: Mapped[str] = mapped_column(Text, nullable=False)
    feature_version: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    code_git_sha: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "feature_name", "feature_version", name="uq_feature_definitions_name_version"
        ),
    )


class FeatureSetDefinition(Base):
    """Versioned collection of exact (feature_name, feature_version) pairs.

    A new version is required whenever any member feature version changes or
    features are added/removed.  content_hash is a deterministic hash of the
    sorted members list and is used to detect accidental duplicates.

    members JSON example:
        [{"name": "revenue_cagr_3y", "version": "v1"}, ...]
    """

    __tablename__ = "feature_set_definitions"

    feature_set_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    members: Mapped[list] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    code_git_sha: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "name", "version", name="uq_feature_set_definitions_name_version"
        ),
    )


class FeatureSnapshot(Base):
    """Immutable computed feature vector for one company/security/as-of date.

    NEVER UPDATE an existing snapshot.  If upstream data is revised:
      1. Insert a new snapshot with the corrected source_lineage.
      2. The new source_lineage_hash will differ from the old one.
      3. Optionally link it via supersedes_snapshot_id.

    The unique constraint on (company_id, security_id, as_of_date,
    feature_set_id, source_lineage_hash) enforces this invariant at the DB level.

    Domain rule: feature construction may NOT use any data with
    available_at > as_of_date.  Labels may use future data; features may not.
    """

    __tablename__ = "feature_snapshots"

    feature_snapshot_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_feature_snapshots_company_id"),
        nullable=False,
    )
    security_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("securities.security_id", name="fk_feature_snapshots_security_id"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    feature_set_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "feature_set_definitions.feature_set_id",
            name="fk_feature_snapshots_feature_set_id",
        ),
        nullable=False,
    )
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_lineage_hash: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    data_quality: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    supersedes_snapshot_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "feature_snapshots.feature_snapshot_id",
            name="fk_feature_snapshots_supersedes",
        ),
        nullable=True,
    )
    dagster_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "security_id",
            "as_of_date",
            "feature_set_id",
            "source_lineage_hash",
            name="uq_feature_snapshots",
        ),
    )
