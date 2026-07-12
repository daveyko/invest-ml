from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from invest_ml.db.base import Base


class RawSourceVersion(Base):
    """Persisted raw payload from an external source for a specific entity.

    Only created for companies that have passed universe selection.
    Broad candidate profiling must NOT insert rows here for every company.

    Either object_uri (pointer to a file in ArtifactStore) or payload
    (inline JSONB) must be non-null.
    """

    __tablename__ = "raw_source_versions"

    raw_version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    entity_key: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "CIK0001679788"
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_runs.run_id", name="fk_raw_source_versions_source_run_id"),
        nullable=True,
    )
    object_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_locator: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "source", "entity_key", "content_hash", name="uq_raw_source_versions"
        ),
        CheckConstraint(
            "object_uri IS NOT NULL OR payload IS NOT NULL",
            name="ck_raw_source_versions_storage",
        ),
    )


class RawVersionDerivation(Base):
    """Tracks the status of a derivation pipeline step for a raw source version.

    PK: (raw_version_id, derivation_type, derivation_version)
    status: running → succeeded | failed
    """

    __tablename__ = "raw_version_derivations"

    raw_version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "raw_source_versions.raw_version_id",
            name="fk_raw_version_derivations_version_id",
        ),
        primary_key=True,
        nullable=False,
    )
    derivation_type: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    derivation_version: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    derivation_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_raw_version_derivations_status",
        ),
        Index("ix_raw_version_derivations_status", "status"),
    )


class XbrlFact(Base):
    """Flattened individual fact from SEC XBRL CompanyFacts.

    fact_id is a deterministic hash over (company_id, taxonomy, tag, unit,
    period_end, filed_date, dimensions) so re-ingesting the same data is
    idempotent.

    Point-in-time rule: use filed_date <= as_of_date when selecting facts.
    """

    __tablename__ = "xbrl_facts"

    fact_id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_xbrl_facts_company_id"),
        nullable=False,
    )
    taxonomy: Mapped[str] = mapped_column(Text, nullable=False)
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric, nullable=False)
    accession_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(Text, nullable=True)
    form: Mapped[str | None] = mapped_column(Text, nullable=True)
    filed_date: Mapped[date] = mapped_column(Date, nullable=False)
    frame: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimensions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    raw_version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_source_versions.raw_version_id", name="fk_xbrl_facts_raw_version_id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_xbrl_facts_company_tag_period", "company_id", "taxonomy", "tag", "period_end"),
        Index("ix_xbrl_facts_company_filed", "company_id", "filed_date"),
        Index("ix_xbrl_facts_accession", "accession_number"),
    )


class CanonicalMetric(Base):
    """Point-in-time normalized financial value derived from XBRL facts.

    Revisions are preserved: when normalization logic changes or upstream
    facts are corrected, a new row is inserted with a new normalization_version
    or available_at.  Old rows are never deleted.

    Point-in-time rule: use available_at <= as_of_date when selecting metrics.
    available_at is the date the metric became known (typically the SEC filing date).
    """

    __tablename__ = "canonical_metrics"

    canonical_metric_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_canonical_metrics_company_id"),
        nullable=False,
    )
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    period_type: Mapped[str] = mapped_column(Text, nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(Text, nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    available_at: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    normalization_version: Mapped[str] = mapped_column(Text, nullable=False)
    source_fact_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    derivation: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    quality_flags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "period_type IN ('quarter', 'annual', 'ttm')",
            name="ck_canonical_metrics_period_type",
        ),
        UniqueConstraint(
            "company_id",
            "metric_name",
            "period_type",
            "period_end",
            "available_at",
            "normalization_version",
            name="uq_canonical_metrics",
        ),
        Index(
            "ix_canonical_metrics_company_metric_available",
            "company_id",
            "metric_name",
            "available_at",
            "period_end",
        ),
    )
