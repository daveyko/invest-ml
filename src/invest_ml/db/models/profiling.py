from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from invest_ml.db.base import Base


class CompanyDataProfile(Base):
    """Lightweight data-quality snapshot produced by scanning CompanyFacts.

    One row per (company, profile_version).  Created during broad candidate
    profiling WITHOUT persisting the full raw CompanyFacts payload.  Used to
    decide which companies have sufficient financial history to join the
    training universe.

    Domain rule: profiling must NOT persist raw CompanyFacts for all companies.
    Heavy persistence is reserved for universe members only (see raw_source_versions).
    """

    __tablename__ = "company_data_profiles"

    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_company_data_profiles_company_id"),
        primary_key=True,
        nullable=False,
    )
    profile_version: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_runs.run_id", name="fk_company_data_profiles_source_run_id"),
        nullable=True,
    )
    first_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_filed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    annual_periods: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    quarterly_periods: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    has_revenue: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_operating_income: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_net_income: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_operating_cash_flow: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_cash: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_debt: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_shares: Mapped[bool] = mapped_column(Boolean, nullable=False)
    canonical_metric_coverage: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    fact_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    quality_flags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class CompanyMarketProfile(Base):
    """Lightweight price/investability snapshot for candidate companies.

    One row per (security, profile_version).  Created during market profiling
    for financially eligible candidates; used to gate training universe membership.
    """

    __tablename__ = "company_market_profiles"

    security_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("securities.security_id", name="fk_company_market_profiles_security_id"),
        primary_key=True,
        nullable=False,
    )
    profile_version: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    first_price_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_price_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    price_history_years: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    median_daily_dollar_volume: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    current_market_cap: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    missing_trading_day_ratio: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    latest_adjusted_close: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    quality_flags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
