"""Verify SQLAlchemy models without connecting to a database.

Tests compile PostgreSQL DDL from metadata and assert all 22 tables are registered.
"""

from sqlalchemy.dialects import postgresql

EXPECTED_TABLES = {
    "ingestion_runs",
    "companies",
    "securities",
    "company_classifications",
    "company_data_profiles",
    "company_market_profiles",
    "universe_definitions",
    "universe_memberships",
    "raw_source_versions",
    "raw_version_derivations",
    "xbrl_facts",
    "canonical_metrics",
    "price_bars",
    "price_bar_sync_state",
    "feature_definitions",
    "feature_set_definitions",
    "feature_set_members",
    "feature_snapshots",
    "target_specs",
    "labels",
    "training_datasets",
    "training_dataset_rows",
    "model_runs",
    "predictions",
}


def _get_metadata():
    import invest_ml.db.models  # noqa: F401 — registers all models
    from invest_ml.db.base import Base

    return Base.metadata


def test_all_tables_registered() -> None:
    meta = _get_metadata()
    registered = set(meta.tables.keys())
    missing = EXPECTED_TABLES - registered
    assert not missing, f"Tables not registered in metadata: {missing}"
    assert len(registered) == len(EXPECTED_TABLES), (
        f"Unexpected extra tables: {registered - EXPECTED_TABLES}"
    )


def test_ddl_compiles_without_connection() -> None:
    """Compile CREATE TABLE statements for every table using the PostgreSQL dialect.

    This catches FK reference errors, column type issues, and constraint naming
    problems without requiring a live database.
    """
    from sqlalchemy.schema import CreateTable

    meta = _get_metadata()
    dialect = postgresql.dialect()
    errors = []
    for table in meta.sorted_tables:
        try:
            ddl = str(CreateTable(table).compile(dialect=dialect))
            assert table.name in ddl
        except Exception as exc:
            errors.append(f"{table.name}: {exc}")
    assert not errors, "DDL compile errors:\n" + "\n".join(errors)


def test_ingestion_run_status_check_exists() -> None:
    meta = _get_metadata()
    table = meta.tables["ingestion_runs"]
    constraint_names = {c.name for c in table.constraints}
    assert "ck_ingestion_runs_status" in constraint_names


def test_feature_snapshot_immutability_unique_constraint() -> None:
    """The unique constraint on feature_snapshots enforces the immutability invariant."""
    meta = _get_metadata()
    table = meta.tables["feature_snapshots"]
    unique_names = {c.name for c in table.constraints}
    assert "uq_feature_snapshots" in unique_names


def test_predictions_probability_check_exists() -> None:
    meta = _get_metadata()
    table = meta.tables["predictions"]
    constraint_names = {c.name for c in table.constraints}
    assert "ck_predictions_probability_range" in constraint_names


def test_raw_source_versions_storage_check_exists() -> None:
    meta = _get_metadata()
    table = meta.tables["raw_source_versions"]
    constraint_names = {c.name for c in table.constraints}
    assert "ck_raw_source_versions_storage" in constraint_names
