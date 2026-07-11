"""Update companies and securities tables to match submissions-feed schema.

companies:  add entity_type, filer_category, latest_filing_date; drop is_active.
securities: add is_currently_reported_by_sec; drop security_type, is_primary,
            is_active; replace ix_securities_active_exchange.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── companies ───────────────────────────────────────────────────────────
    op.add_column("companies", sa.Column("entity_type", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("filer_category", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("latest_filing_date", sa.Date(), nullable=True))
    op.drop_column("companies", "is_active")

    # ── securities ──────────────────────────────────────────────────────────
    op.drop_index("ix_securities_active_exchange", table_name="securities")
    op.drop_column("securities", "security_type")
    op.drop_column("securities", "is_primary")
    op.drop_column("securities", "is_active")
    op.add_column(
        "securities",
        sa.Column(
            "is_currently_reported_by_sec",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.create_index(
        "ix_securities_exchange_reported",
        "securities",
        ["exchange", "is_currently_reported_by_sec"],
    )


def downgrade() -> None:
    # ── securities ──────────────────────────────────────────────────────────
    op.drop_index("ix_securities_exchange_reported", table_name="securities")
    op.drop_column("securities", "is_currently_reported_by_sec")
    op.add_column(
        "securities",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "securities",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column("securities", sa.Column("security_type", sa.Text(), nullable=True))
    op.create_index(
        "ix_securities_active_exchange", "securities", ["is_active", "exchange"]
    )

    # ── companies ───────────────────────────────────────────────────────────
    op.drop_column("companies", "latest_filing_date")
    op.drop_column("companies", "filer_category")
    op.drop_column("companies", "entity_type")
    op.add_column(
        "companies",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
