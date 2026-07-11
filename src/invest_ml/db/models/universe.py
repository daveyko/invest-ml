from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from invest_ml.db.base import Base


class UniverseDefinition(Base):
    """Versioned definition of a named company universe.

    Purpose must be one of:
      candidate  - all current US-listed operating companies worth profiling
      training   - broad companies with sufficient financial and market history
      scoring    - narrower AI/crypto/adjacent universe used for predictions
      personal_watchlist - ad-hoc user-defined list

    Criteria is the machine-readable filter specification stored as JSONB.
    """

    __tablename__ = "universe_definitions"

    universe_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    criteria: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('candidate', 'training', 'scoring', 'personal_watchlist')",
            name="ck_universe_definitions_purpose",
        ),
        UniqueConstraint("name", "version", name="uq_universe_definitions_name_version"),
    )


class UniverseMembership(Base):
    """Effective-dated membership of a company in a universe.

    Composite PK on (universe_id, company_id, included_from) so membership
    intervals are preserved and comparable across universe versions.
    """

    __tablename__ = "universe_memberships"

    universe_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("universe_definitions.universe_id", name="fk_universe_memberships_universe_id"),
        primary_key=True,
        nullable=False,
    )
    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_universe_memberships_company_id"),
        primary_key=True,
        nullable=False,
    )
    security_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("securities.security_id", name="fk_universe_memberships_security_id"),
        nullable=True,
    )
    included_from: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    included_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    inclusion_reasons: Mapped[dict] = mapped_column(JSONB, nullable=False)
    exclusion_reasons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
