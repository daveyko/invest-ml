"""Source-reference tests for the feature registry.

Verifies that every canonical-metric and price-bar field referenced in
compounder_v1.yaml actually exists in the project's authoritative registries.

No database connections or external API calls are made.
"""

from invest_ml.config.loaders import load_canonical_metrics, load_feature_registry_config
from invest_ml.features.validator import VALID_PRICE_BAR_FIELDS, validate_registry


def _load_raw() -> dict:
    return load_feature_registry_config("compounder", "v1")


def _known_metrics() -> set[str]:
    cfg = load_canonical_metrics()
    return set((cfg.get("metrics") or {}).keys())


def _price_bar_columns() -> set[str]:
    from invest_ml.db.models.market import PriceBar
    excluded = {"price_bar_id", "security_id", "trading_date"}
    return {col.name for col in PriceBar.__table__.columns} - excluded


def _extract_all(node, key: str, results: set[str]) -> None:
    if isinstance(node, dict):
        if key in node:
            results.add(node[key])
        for v in node.values():
            _extract_all(v, key, results)
    elif isinstance(node, list):
        for item in node:
            _extract_all(item, key, results)


# ── Canonical metric references ────────────────────────────────────────────────


def test_all_referenced_canonical_metrics_exist():
    """Every metric_name in the registry must be declared in canonical_metrics_v1."""
    raw = _load_raw()
    known = _known_metrics()
    errors = []
    for fname, fspec in (raw.get("features") or {}).items():
        metric_names: set[str] = set()
        _extract_all(fspec.get("definition", {}), "metric_name", metric_names)
        for m in metric_names:
            if m not in known:
                errors.append(f"Feature '{fname}' references unknown metric '{m}'")
    assert not errors, "\n".join(errors)


# ── Price-bar field references ─────────────────────────────────────────────────


def test_all_referenced_price_fields_exist_in_model():
    """Every price_bar field referenced in definitions must exist in PriceBar model."""
    raw = _load_raw()
    model_columns = _price_bar_columns()
    errors = []
    for fname, fspec in (raw.get("features") or {}).items():
        defn = fspec.get("definition", {})
        fields: set[str] = set()
        _extract_all(defn, "field", fields)
        _extract_all(defn, "close_field", fields)
        _extract_all(defn, "volume_field", fields)
        for f in fields:
            if f not in model_columns:
                errors.append(f"Feature '{fname}' references unknown price_bar field '{f}'")
    assert not errors, "\n".join(errors)


def test_price_bar_columns_match_validator_allowlist():
    """The validator's VALID_PRICE_BAR_FIELDS must be a subset of actual model columns."""
    model_columns = _price_bar_columns()
    not_in_model = VALID_PRICE_BAR_FIELDS - model_columns
    assert not not_in_model, (
        f"Validator allows fields not in PriceBar model: {not_in_model}"
    )


def test_tiingo_field_names_not_in_registry():
    """Raw Tiingo JSON field names must never appear in the feature registry."""
    tiingo_names = {"adjClose", "adjOpen", "adjHigh", "adjLow", "adjVolume", "divCash"}
    raw = _load_raw()
    all_fields: set[str] = set()
    for fspec in (raw.get("features") or {}).values():
        _extract_all(fspec.get("definition", {}), "field", all_fields)
        _extract_all(fspec.get("definition", {}), "close_field", all_fields)
        _extract_all(fspec.get("definition", {}), "volume_field", all_fields)
    leaked = tiingo_names & all_fields
    assert not leaked, f"Tiingo field names leaked into registry: {leaked}"


# ── Point-in-time policy ────────────────────────────────────────────────────────


def test_fundamental_features_use_available_at():
    """All canonical-metric features must declare available_at availability."""
    raw = _load_raw()
    for fname, fspec in (raw.get("features") or {}).items():
        metric_names: set[str] = set()
        _extract_all(fspec.get("definition", {}), "metric_name", metric_names)
        if not metric_names:
            continue
        pit = fspec.get("point_in_time_policy", {})
        assert pit.get("availability_field") == "available_at", (
            f"Feature '{fname}' uses canonical metrics but point_in_time_policy "
            f"does not declare availability_field=available_at: {pit}"
        )
        assert "on_or_before" in pit.get("selection", ""), (
            f"Feature '{fname}': selection must use on_or_before semantics: {pit}"
        )


def test_price_features_use_trading_date():
    """All price_bar features must declare trading_date availability."""
    raw = _load_raw()
    for fname, fspec in (raw.get("features") or {}).items():
        sources: set[str] = set()
        _extract_all(fspec.get("definition", {}), "source", sources)
        if "price_bar" not in sources:
            continue
        pit = fspec.get("point_in_time_policy", {})
        assert pit.get("availability_field") == "trading_date", (
            f"Feature '{fname}' uses price_bar but point_in_time_policy "
            f"does not declare availability_field=trading_date: {pit}"
        )
        assert "on_or_before" in pit.get("selection", ""), (
            f"Feature '{fname}': selection must use on_or_before semantics: {pit}"
        )


def test_no_feature_allows_future_filling():
    """No feature selection policy should allow forward-filling from future dates."""
    raw = _load_raw()
    forbidden_terms = {"forward_fill", "future", "after_as_of"}
    for fname, fspec in (raw.get("features") or {}).items():
        pit = fspec.get("point_in_time_policy", {})
        selection = pit.get("selection", "")
        for term in forbidden_terms:
            assert term not in selection, (
                f"Feature '{fname}' point_in_time_policy contains forbidden term "
                f"'{term}': {pit}"
            )


def test_validation_passes_without_warehouse_rows():
    """Registry validation must not require any rows to exist in the database."""
    raw = _load_raw()
    known = _known_metrics()
    # If this raises, it implies a live DB query was attempted.
    validate_registry(raw, known_canonical_metrics=known)
