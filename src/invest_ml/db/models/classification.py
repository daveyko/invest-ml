from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from invest_ml.db.base import Base


class CompanyClassification(Base):
    """Taxonomy classification for a company.

    Supports SEC SIC codes (taxonomy='sec_sic'), model buckets derived from
    SIC mappings (taxonomy='model_bucket'), and optional thematic groupings
    (taxonomy='theme').  Effective-dated so changes are preserved over time.
    """

    __tablename__ = "company_classifications"

    classification_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_company_classifications_company_id"),
        nullable=False,
    )
    taxonomy: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    classifier_version: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    classification_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        CheckConstraint(
            "taxonomy IN ('sec_sic', 'model_bucket', 'theme')",
            name="ck_company_classifications_taxonomy",
        ),
        CheckConstraint(
            "source IN ('sec', 'sic_mapping', 'keyword_rule', 'manual_override')",
            name="ck_company_classifications_source",
        ),
        UniqueConstraint(
            "company_id",
            "taxonomy",
            "code",
            "classifier_version",
            "effective_from",
            name="uq_company_classifications",
        ),
    )
