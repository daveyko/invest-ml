"""Extend feature_definitions and feature_set_definitions; add feature_set_members.

Adds new columns required by the feature registry:
- feature_definitions: category, entity_grain, value_type, point_in_time_policy,
  missing_value_policy, configuration_hash; makes code_git_sha nullable.
- feature_set_definitions: entity_grain, snapshot_frequency, description, status,
  configuration_hash; makes members, content_hash, code_git_sha nullable.
- New table: feature_set_members (ordered membership association).

Revision ID: 0007
Revises: 0006
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── feature_definitions: make code_git_sha nullable ──────────────────────
    op.alter_column("feature_definitions", "code_git_sha", nullable=True)

    # ── feature_definitions: add new columns ─────────────────────────────────
    op.add_column("feature_definitions", sa.Column("category", sa.Text(), nullable=True))
    op.add_column("feature_definitions", sa.Column("entity_grain", sa.Text(), nullable=True))
    op.add_column("feature_definitions", sa.Column("value_type", sa.Text(), nullable=True))
    op.add_column(
        "feature_definitions",
        sa.Column(
            "point_in_time_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "feature_definitions",
        sa.Column(
            "missing_value_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "feature_definitions",
        sa.Column("configuration_hash", sa.Text(), nullable=True),
    )

    # ── feature_set_definitions: drop legacy members column, make others nullable ─
    op.drop_column("feature_set_definitions", "members")
    op.alter_column("feature_set_definitions", "content_hash", nullable=True)
    op.alter_column("feature_set_definitions", "code_git_sha", nullable=True)

    # ── feature_set_definitions: add new columns ──────────────────────────────
    op.add_column("feature_set_definitions", sa.Column("entity_grain", sa.Text(), nullable=True))
    op.add_column(
        "feature_set_definitions", sa.Column("snapshot_frequency", sa.Text(), nullable=True)
    )
    op.add_column("feature_set_definitions", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("feature_set_definitions", sa.Column("status", sa.Text(), nullable=True))
    op.add_column(
        "feature_set_definitions",
        sa.Column("configuration_hash", sa.Text(), nullable=True),
    )

    # ── feature_set_members ───────────────────────────────────────────────────
    op.create_table(
        "feature_set_members",
        sa.Column(
            "feature_set_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "feature_definition_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["feature_set_id"],
            ["feature_set_definitions.feature_set_id"],
            name="fk_feature_set_members_set_id",
        ),
        sa.ForeignKeyConstraint(
            ["feature_definition_id"],
            ["feature_definitions.feature_definition_id"],
            name="fk_feature_set_members_def_id",
        ),
        sa.PrimaryKeyConstraint("feature_set_id", "feature_definition_id"),
    )


def downgrade() -> None:
    op.drop_table("feature_set_members")

    # Remove added columns from feature_set_definitions
    for col in ("configuration_hash", "status", "description", "snapshot_frequency", "entity_grain"):
        op.drop_column("feature_set_definitions", col)
    op.alter_column("feature_set_definitions", "code_git_sha", nullable=False)
    op.alter_column("feature_set_definitions", "content_hash", nullable=False)
    op.add_column(
        "feature_set_definitions",
        sa.Column("members", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )

    # Remove added columns from feature_definitions
    for col in ("configuration_hash", "missing_value_policy", "point_in_time_policy",
                "value_type", "entity_grain", "category"):
        op.drop_column("feature_definitions", col)
    op.alter_column("feature_definitions", "code_git_sha", nullable=False)
