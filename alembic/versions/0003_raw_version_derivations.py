"""Add source_locator to raw_source_versions; create raw_version_derivations table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── raw_source_versions: add source_locator ─────────────────────────────
    op.add_column(
        "raw_source_versions",
        sa.Column(
            "source_locator",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_raw_source_versions_entity_key",
        "raw_source_versions",
        ["entity_key"],
    )

    # ── raw_version_derivations ──────────────────────────────────────────────
    op.create_table(
        "raw_version_derivations",
        sa.Column(
            "raw_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "raw_source_versions.raw_version_id",
                name="fk_raw_version_derivations_version_id",
            ),
            nullable=False,
        ),
        sa.Column("derivation_type", sa.Text(), nullable=False),
        sa.Column("derivation_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint(
            "raw_version_id",
            "derivation_type",
            "derivation_version",
            name="pk_raw_version_derivations",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_raw_version_derivations_status",
        ),
    )
    op.create_index(
        "ix_raw_version_derivations_status",
        "raw_version_derivations",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_version_derivations_status", table_name="raw_version_derivations")
    op.drop_table("raw_version_derivations")
    op.drop_index("ix_raw_source_versions_entity_key", table_name="raw_source_versions")
    op.drop_column("raw_source_versions", "source_locator")
