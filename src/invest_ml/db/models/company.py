from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from invest_ml.db.base import Base


class Company(Base):
    """Lightweight company metadata populated from SEC submissions.

    Stored for ALL companies observed in the SEC feed; heavy financial data
    is gated behind universe selection.
    """

    __tablename__ = "companies"

    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    cik: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)

    # Lightweight SEC metadata — all nullable; populated when present in submissions.
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    filer_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiscal_year_end: Mapped[str | None] = mapped_column(String(4), nullable=True)
    state_of_incorporation: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_filing_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_runs.run_id", name="fk_companies_source_run_id"),
        nullable=True,
    )

    securities: Mapped[list["Security"]] = relationship("Security", back_populates="company")


class Security(Base):
    """A publicly traded ticker/exchange pair reported by SEC for a company.

    is_currently_reported_by_sec reflects whether the ticker appeared in the
    most recent full submissions archive.  It is set to False only after a
    complete, successful archive run confirms the ticker is no longer reported.
    """

    __tablename__ = "securities"

    security_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_securities_company_id"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_currently_reported_by_sec: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    company: Mapped["Company"] = relationship("Company", back_populates="securities")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "ticker", "exchange",
            name="uq_securities_company_ticker_exchange",
        ),
        Index("ix_securities_ticker", "ticker"),
        Index("ix_securities_exchange_reported", "exchange", "is_currently_reported_by_sec"),
    )
