"""Add indexes for canonical_metrics normalization queries.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # Supports the stream_candidate_facts query: WHERE (taxonomy, tag) IN (...) AND company_id IN (...)
    op.create_index(
        "ix_xbrl_facts_taxonomy_tag_company",
        "xbrl_facts",
        ["taxonomy", "tag", "company_id"],
    )

    # Supports metric-level analytical queries across all companies
    op.create_index(
        "ix_canonical_metrics_metric_period",
        "canonical_metrics",
        ["metric_name", "period_type", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_metrics_metric_period", table_name="canonical_metrics")
    op.drop_index("ix_xbrl_facts_taxonomy_tag_company", table_name="xbrl_facts")
