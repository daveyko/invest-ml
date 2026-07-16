"""Add as_of_date to universe_definitions for monthly-partitioned training universe.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add as_of_date column (nullable — existing non-partitioned rows keep NULL)
    op.add_column(
        "universe_definitions",
        sa.Column("as_of_date", sa.Date, nullable=True),
    )

    # Drop the old unique constraint (name, version)
    op.drop_constraint("uq_universe_definitions_name_version", "universe_definitions")

    # New unique constraint: (name, version, as_of_date).
    # For non-null as_of_date rows this enforces uniqueness at DB level.
    # Existing NULL rows rely on application-level duplicate prevention
    # (the service always calls find_universe_definition before creating).
    op.create_unique_constraint(
        "uq_universe_definitions_name_version_date",
        "universe_definitions",
        ["name", "version", "as_of_date"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_universe_definitions_name_version_date", "universe_definitions")
    op.create_unique_constraint(
        "uq_universe_definitions_name_version",
        "universe_definitions",
        ["name", "version"],
    )
    op.drop_column("universe_definitions", "as_of_date")
