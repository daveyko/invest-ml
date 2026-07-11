"""Initial schema: all 21 tables with constraints and indexes.

Revision ID: 0001
Revises:
Create Date: 2026-07-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ingestion_runs ──────────────────────────────────────────────────────
    op.create_table(
        "ingestion_runs",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archive_hash", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("entities_checked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("entities_changed", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_ingestion_runs_status",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_ingestion_runs_source_started_at",
        "ingestion_runs",
        ["source", sa.text("started_at DESC")],
    )

    # ── companies ───────────────────────────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("fiscal_year_end", sa.String(4), nullable=True),
        sa.Column("state_of_incorporation", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["ingestion_runs.run_id"],
            name="fk_companies_source_run_id",
        ),
        sa.PrimaryKeyConstraint("company_id"),
        sa.UniqueConstraint("cik"),
    )

    # ── securities ──────────────────────────────────────────────────────────
    op.create_table(
        "securities",
        sa.Column(
            "security_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=True),
        sa.Column("security_type", sa.Text(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_securities_company_id",
        ),
        sa.PrimaryKeyConstraint("security_id"),
        sa.UniqueConstraint(
            "company_id", "ticker", "exchange", name="uq_securities_company_ticker_exchange"
        ),
    )
    op.create_index("ix_securities_ticker", "securities", ["ticker"])
    op.create_index(
        "ix_securities_active_exchange", "securities", ["is_active", "exchange"]
    )

    # ── company_classifications ─────────────────────────────────────────────
    op.create_table(
        "company_classifications",
        sa.Column(
            "classification_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taxonomy", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("classifier_version", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "taxonomy IN ('sec_sic', 'model_bucket', 'theme')",
            name="ck_company_classifications_taxonomy",
        ),
        sa.CheckConstraint(
            "source IN ('sec', 'sic_mapping', 'keyword_rule', 'manual_override')",
            name="ck_company_classifications_source",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_company_classifications_company_id",
        ),
        sa.PrimaryKeyConstraint("classification_id"),
        sa.UniqueConstraint(
            "company_id",
            "taxonomy",
            "code",
            "classifier_version",
            "effective_from",
            name="uq_company_classifications",
        ),
    )

    # ── company_data_profiles ───────────────────────────────────────────────
    op.create_table(
        "company_data_profiles",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_period_end", sa.Date(), nullable=True),
        sa.Column("latest_period_end", sa.Date(), nullable=True),
        sa.Column("latest_filed_date", sa.Date(), nullable=True),
        sa.Column("annual_periods", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quarterly_periods", sa.Integer(), server_default="0", nullable=False),
        sa.Column("has_revenue", sa.Boolean(), nullable=False),
        sa.Column("has_operating_income", sa.Boolean(), nullable=False),
        sa.Column("has_net_income", sa.Boolean(), nullable=False),
        sa.Column("has_operating_cash_flow", sa.Boolean(), nullable=False),
        sa.Column("has_cash", sa.Boolean(), nullable=False),
        sa.Column("has_debt", sa.Boolean(), nullable=False),
        sa.Column("has_shares", sa.Boolean(), nullable=False),
        sa.Column("canonical_metric_coverage", sa.Numeric(), nullable=True),
        sa.Column("fact_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_company_data_profiles_company_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["ingestion_runs.run_id"],
            name="fk_company_data_profiles_source_run_id",
        ),
        sa.PrimaryKeyConstraint("company_id", "profile_version"),
    )

    # ── company_market_profiles ─────────────────────────────────────────────
    op.create_table(
        "company_market_profiles",
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("first_price_date", sa.Date(), nullable=True),
        sa.Column("latest_price_date", sa.Date(), nullable=True),
        sa.Column("price_history_years", sa.Numeric(), nullable=True),
        sa.Column("median_daily_dollar_volume", sa.Numeric(), nullable=True),
        sa.Column("current_market_cap", sa.Numeric(), nullable=True),
        sa.Column("missing_trading_day_ratio", sa.Numeric(), nullable=True),
        sa.Column("latest_adjusted_close", sa.Numeric(), nullable=True),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.security_id"],
            name="fk_company_market_profiles_security_id",
        ),
        sa.PrimaryKeyConstraint("security_id", "profile_version"),
    )

    # ── universe_definitions ────────────────────────────────────────────────
    op.create_table(
        "universe_definitions",
        sa.Column(
            "universe_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('candidate', 'training', 'scoring', 'personal_watchlist')",
            name="ck_universe_definitions_purpose",
        ),
        sa.PrimaryKeyConstraint("universe_id"),
        sa.UniqueConstraint("name", "version", name="uq_universe_definitions_name_version"),
    )

    # ── universe_memberships ────────────────────────────────────────────────
    op.create_table(
        "universe_memberships",
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("included_from", sa.Date(), nullable=False),
        sa.Column("included_until", sa.Date(), nullable=True),
        sa.Column("inclusion_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("exclusion_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_universe_memberships_company_id",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.security_id"],
            name="fk_universe_memberships_security_id",
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universe_definitions.universe_id"],
            name="fk_universe_memberships_universe_id",
        ),
        sa.PrimaryKeyConstraint("universe_id", "company_id", "included_from"),
    )

    # ── raw_source_versions ─────────────────────────────────────────────────
    op.create_table(
        "raw_source_versions",
        sa.Column(
            "raw_version_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("entity_key", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("object_uri", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "object_uri IS NOT NULL OR payload IS NOT NULL",
            name="ck_raw_source_versions_storage",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["ingestion_runs.run_id"],
            name="fk_raw_source_versions_source_run_id",
        ),
        sa.PrimaryKeyConstraint("raw_version_id"),
        sa.UniqueConstraint(
            "source", "entity_key", "content_hash", name="uq_raw_source_versions"
        ),
    )

    # ── xbrl_facts ──────────────────────────────────────────────────────────
    op.create_table(
        "xbrl_facts",
        sa.Column("fact_id", sa.Text(), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taxonomy", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("accession_number", sa.Text(), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.Text(), nullable=True),
        sa.Column("form", sa.Text(), nullable=True),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("frame", sa.Text(), nullable=True),
        sa.Column(
            "dimensions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_xbrl_facts_company_id",
        ),
        sa.ForeignKeyConstraint(
            ["raw_version_id"],
            ["raw_source_versions.raw_version_id"],
            name="fk_xbrl_facts_raw_version_id",
        ),
        sa.PrimaryKeyConstraint("fact_id"),
    )
    op.create_index(
        "ix_xbrl_facts_company_tag_period",
        "xbrl_facts",
        ["company_id", "taxonomy", "tag", "period_end"],
    )
    op.create_index(
        "ix_xbrl_facts_company_filed", "xbrl_facts", ["company_id", "filed_date"]
    )
    op.create_index("ix_xbrl_facts_accession", "xbrl_facts", ["accession_number"])

    # ── canonical_metrics ───────────────────────────────────────────────────
    op.create_table(
        "canonical_metrics",
        sa.Column(
            "canonical_metric_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("period_type", sa.Text(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.Text(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("available_at", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("normalization_version", sa.Text(), nullable=False),
        sa.Column("source_fact_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "derivation",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "period_type IN ('quarter', 'annual', 'ttm')",
            name="ck_canonical_metrics_period_type",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_canonical_metrics_company_id",
        ),
        sa.PrimaryKeyConstraint("canonical_metric_id"),
        sa.UniqueConstraint(
            "company_id",
            "metric_name",
            "period_type",
            "period_end",
            "available_at",
            "normalization_version",
            name="uq_canonical_metrics",
        ),
    )
    op.create_index(
        "ix_canonical_metrics_company_metric_available",
        "canonical_metrics",
        ["company_id", "metric_name", "available_at", "period_end"],
    )

    # ── price_bars ──────────────────────────────────────────────────────────
    op.create_table(
        "price_bars",
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=True),
        sa.Column("high", sa.Numeric(), nullable=True),
        sa.Column("low", sa.Numeric(), nullable=True),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("adjusted_close", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=True),
        sa.Column("market_cap", sa.Numeric(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.security_id"],
            name="fk_price_bars_security_id",
        ),
        sa.PrimaryKeyConstraint("security_id", "trading_date", "source"),
    )

    # ── feature_definitions ─────────────────────────────────────────────────
    op.create_table(
        "feature_definitions",
        sa.Column(
            "feature_definition_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("feature_name", sa.Text(), nullable=False),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("code_git_sha", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("feature_definition_id"),
        sa.UniqueConstraint(
            "feature_name", "feature_version", name="uq_feature_definitions_name_version"
        ),
    )

    # ── feature_set_definitions ─────────────────────────────────────────────
    op.create_table(
        "feature_set_definitions",
        sa.Column(
            "feature_set_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("members", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("code_git_sha", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("feature_set_id"),
        sa.UniqueConstraint(
            "name", "version", name="uq_feature_set_definitions_name_version"
        ),
    )

    # ── feature_snapshots ───────────────────────────────────────────────────
    op.create_table(
        "feature_snapshots",
        sa.Column(
            "feature_snapshot_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("feature_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_lineage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_lineage_hash", sa.Text(), nullable=False),
        sa.Column("snapshot_hash", sa.Text(), nullable=False),
        sa.Column(
            "data_quality",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("supersedes_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dagster_run_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_feature_snapshots_company_id",
        ),
        sa.ForeignKeyConstraint(
            ["feature_set_id"],
            ["feature_set_definitions.feature_set_id"],
            name="fk_feature_snapshots_feature_set_id",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.security_id"],
            name="fk_feature_snapshots_security_id",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_snapshot_id"],
            ["feature_snapshots.feature_snapshot_id"],
            name="fk_feature_snapshots_supersedes",
        ),
        sa.PrimaryKeyConstraint("feature_snapshot_id"),
        sa.UniqueConstraint(
            "company_id",
            "security_id",
            "as_of_date",
            "feature_set_id",
            "source_lineage_hash",
            name="uq_feature_snapshots",
        ),
    )

    # ── target_specs ────────────────────────────────────────────────────────
    op.create_table(
        "target_specs",
        sa.Column(
            "target_spec_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("horizon_months", sa.Integer(), nullable=False),
        sa.Column("return_threshold", sa.Numeric(), nullable=False),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("horizon_months > 0", name="ck_target_specs_horizon_months"),
        sa.PrimaryKeyConstraint("target_spec_id"),
        sa.UniqueConstraint("name", "version", name="uq_target_specs_name_version"),
    )

    # ── labels ──────────────────────────────────────────────────────────────
    op.create_table(
        "labels",
        sa.Column(
            "label_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("target_spec_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_trading_date", sa.Date(), nullable=False),
        sa.Column("end_trading_date", sa.Date(), nullable=False),
        sa.Column("start_adjusted_price", sa.Numeric(), nullable=False),
        sa.Column("end_adjusted_price", sa.Numeric(), nullable=False),
        sa.Column("realized_return", sa.Numeric(), nullable=False),
        sa.Column("label", sa.Boolean(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.company_id"], name="fk_labels_company_id"
        ),
        sa.ForeignKeyConstraint(
            ["security_id"], ["securities.security_id"], name="fk_labels_security_id"
        ),
        sa.ForeignKeyConstraint(
            ["target_spec_id"],
            ["target_specs.target_spec_id"],
            name="fk_labels_target_spec_id",
        ),
        sa.PrimaryKeyConstraint("label_id"),
        sa.UniqueConstraint(
            "company_id",
            "security_id",
            "as_of_date",
            "target_spec_id",
            name="uq_labels",
        ),
    )

    # ── training_datasets ───────────────────────────────────────────────────
    op.create_table(
        "training_datasets",
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_spec_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=True),
        sa.Column("train_end", sa.Date(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("column_count", sa.Integer(), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("build_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["feature_set_id"],
            ["feature_set_definitions.feature_set_id"],
            name="fk_training_datasets_feature_set_id",
        ),
        sa.ForeignKeyConstraint(
            ["target_spec_id"],
            ["target_specs.target_spec_id"],
            name="fk_training_datasets_target_spec_id",
        ),
        sa.ForeignKeyConstraint(
            ["universe_id"],
            ["universe_definitions.universe_id"],
            name="fk_training_datasets_universe_id",
        ),
        sa.PrimaryKeyConstraint("dataset_id"),
        sa.UniqueConstraint("name", "version", name="uq_training_datasets_name_version"),
    )

    # ── training_dataset_rows ───────────────────────────────────────────────
    op.create_table(
        "training_dataset_rows",
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "split IN ('train', 'validation', 'test')",
            name="ck_training_dataset_rows_split",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["training_datasets.dataset_id"],
            name="fk_training_dataset_rows_dataset_id",
        ),
        sa.ForeignKeyConstraint(
            ["feature_snapshot_id"],
            ["feature_snapshots.feature_snapshot_id"],
            name="fk_training_dataset_rows_snapshot_id",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"], ["labels.label_id"], name="fk_training_dataset_rows_label_id"
        ),
        sa.PrimaryKeyConstraint("dataset_id", "feature_snapshot_id"),
        sa.UniqueConstraint(
            "dataset_id", "label_id", name="uq_training_dataset_rows_dataset_label"
        ),
    )

    # ── model_runs ──────────────────────────────────────────────────────────
    op.create_table(
        "model_runs",
        sa.Column(
            "model_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("model_type", sa.Text(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("artifact_hash", sa.Text(), nullable=False),
        sa.Column("hyperparameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("git_sha", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('candidate', 'promoted', 'rejected', 'retired')",
            name="ck_model_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["training_datasets.dataset_id"],
            name="fk_model_runs_dataset_id",
        ),
        sa.PrimaryKeyConstraint("model_id"),
        sa.UniqueConstraint("model_name", "model_version", name="uq_model_runs_name_version"),
    )

    # ── predictions ─────────────────────────────────────────────────────────
    op.create_table(
        "predictions",
        sa.Column(
            "prediction_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("probability", sa.Numeric(), nullable=False),
        sa.Column("universe_rank", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "probability >= 0 AND probability <= 1",
            name="ck_predictions_probability_range",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.company_id"], name="fk_predictions_company_id"
        ),
        sa.ForeignKeyConstraint(
            ["feature_snapshot_id"],
            ["feature_snapshots.feature_snapshot_id"],
            name="fk_predictions_feature_snapshot_id",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["model_runs.model_id"], name="fk_predictions_model_id"
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.security_id"],
            name="fk_predictions_security_id",
        ),
        sa.PrimaryKeyConstraint("prediction_id"),
        sa.UniqueConstraint(
            "model_id",
            "security_id",
            "prediction_date",
            name="uq_predictions_model_security_date",
        ),
    )


def downgrade() -> None:
    op.drop_table("predictions")
    op.drop_table("model_runs")
    op.drop_table("training_dataset_rows")
    op.drop_table("training_datasets")
    op.drop_table("labels")
    op.drop_table("target_specs")
    op.drop_table("feature_snapshots")
    op.drop_table("feature_set_definitions")
    op.drop_table("feature_definitions")
    op.drop_table("price_bars")
    op.drop_index("ix_canonical_metrics_company_metric_available", "canonical_metrics")
    op.drop_table("canonical_metrics")
    op.drop_index("ix_xbrl_facts_accession", "xbrl_facts")
    op.drop_index("ix_xbrl_facts_company_filed", "xbrl_facts")
    op.drop_index("ix_xbrl_facts_company_tag_period", "xbrl_facts")
    op.drop_table("xbrl_facts")
    op.drop_table("raw_source_versions")
    op.drop_table("universe_memberships")
    op.drop_table("universe_definitions")
    op.drop_table("company_market_profiles")
    op.drop_table("company_data_profiles")
    op.drop_table("company_classifications")
    op.drop_index("ix_securities_active_exchange", "securities")
    op.drop_index("ix_securities_ticker", "securities")
    op.drop_table("securities")
    op.drop_table("companies")
    op.drop_index("ix_ingestion_runs_source_started_at", "ingestion_runs")
    op.drop_table("ingestion_runs")
