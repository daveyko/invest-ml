"""Feature registry validator.

Validates feature definitions against:
- An allowlist of supported operations.
- An allowlist of supported source types.
- The canonical metrics declared in canonical_metrics_v1.
- The normalized price_bars schema columns.

Does NOT execute any feature computation or read warehouse rows.
"""

from __future__ import annotations

from typing import Any

SUPPORTED_OPERATIONS: frozenset[str] = frozenset(
    {
        "identity",
        "categorical_lookup",
        "ratio",
        "difference",
        "sum",
        "log1p",
        "pct_change",
        "cagr",
        "positive_count",
        "adjusted_return",
        "momentum_excluding_recent",
        "realized_volatility",
        "downside_volatility",
        "max_drawdown",
        "distance_from_high",
        "moving_average_ratio",
        "median_dollar_volume",
        "window_ratio",
        "age_days",
    }
)

SUPPORTED_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "canonical_metric",
        "price_bar",
        "company_classification",
        "derived",
    }
)

# Normalized warehouse columns — must match invest_ml.db.models.market.PriceBar.
VALID_PRICE_BAR_FIELDS: frozenset[str] = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjusted_volume",
        "dividend_cash",
        "split_factor",
    }
)

# Raw provider JSON field names that must never appear in feature definitions.
_REJECTED_PROVIDER_FIELDS: frozenset[str] = frozenset(
    {
        "adjClose",
        "adjOpen",
        "adjHigh",
        "adjLow",
        "adjVolume",
        "divCash",
        "splitFactor",
    }
)

_POSITIVE_LOOKBACK_KEYS: frozenset[str] = frozenset(
    {
        "lookback_observations",
        "minimum_observations",
        "lookback_returns",
        "minimum_periods",
        "lookback_periods",
        "window",
        "total_lookback",
        "recent_exclusion",
        "short_window",
        "long_window",
        "minimum_long_observations",
        "periods",
        "minimum_negative_observations",
    }
)


class RegistryValidationError(ValueError):
    """Raised when feature registry validation fails."""


def validate_registry(
    raw: dict[str, Any],
    *,
    known_canonical_metrics: set[str],
) -> None:
    """Validate the raw feature registry dict.

    Raises RegistryValidationError on any violation.
    Does not write to the database or call external APIs.
    """
    errors: list[str] = []

    if not raw.get("registry_version"):
        errors.append("registry_version is missing or empty")

    raw_features: dict[str, Any] = raw.get("features") or {}
    raw_feature_sets: dict[str, Any] = raw.get("feature_sets") or {}

    # Duplicate feature names are impossible in YAML dicts, but validate
    # that every feature has a version.
    for fname, fspec in raw_features.items():
        if not isinstance(fspec, dict):
            errors.append(f"Feature '{fname}': must be a mapping")
            continue
        if not fspec.get("version"):
            errors.append(f"Feature '{fname}': missing 'version'")
        definition = fspec.get("definition") or {}
        _validate_definition(fname, definition, known_canonical_metrics, errors)

    # Validate feature sets.
    for fs_key, fsspec in raw_feature_sets.items():
        if not isinstance(fsspec, dict):
            errors.append(f"Feature set '{fs_key}': must be a mapping")
            continue
        if not fsspec.get("version"):
            errors.append(f"Feature set '{fs_key}': missing 'version'")

        feature_names: list[str] = fsspec.get("features") or []
        seen: set[str] = set()
        for fn in feature_names:
            if fn in seen:
                errors.append(f"Feature set '{fs_key}': duplicate member '{fn}'")
            seen.add(fn)
            if fn not in raw_features:
                errors.append(
                    f"Feature set '{fs_key}': member '{fn}' not declared in features"
                )

    if errors:
        raise RegistryValidationError(
            f"Feature registry validation failed ({len(errors)} error(s)):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def _validate_definition(
    fname: str,
    definition: dict[str, Any],
    known_canonical_metrics: set[str],
    errors: list[str],
) -> None:
    """Recursively validate a feature definition dict."""
    if not definition:
        return

    _collect_errors_recursive(fname, definition, known_canonical_metrics, errors)


def _collect_errors_recursive(
    fname: str,
    node: Any,
    known_metrics: set[str],
    errors: list[str],
) -> None:
    if not isinstance(node, dict):
        return

    # Validate operation
    if "operation" in node:
        op = node["operation"]
        if op not in SUPPORTED_OPERATIONS:
            errors.append(
                f"Feature '{fname}': unsupported operation '{op}'. "
                f"Supported: {sorted(SUPPORTED_OPERATIONS)}"
            )

    # Validate source type
    if "source" in node:
        src = node["source"]
        if src not in SUPPORTED_SOURCE_TYPES:
            errors.append(
                f"Feature '{fname}': unsupported source type '{src}'. "
                f"Supported: {sorted(SUPPORTED_SOURCE_TYPES)}"
            )
        if src == "canonical_metric":
            metric = node.get("metric_name", "")
            if metric and metric not in known_metrics:
                errors.append(
                    f"Feature '{fname}': unknown canonical metric '{metric}'. "
                    f"Declare it in canonical_metrics_v1.yaml first."
                )
        if src == "price_bar":
            field = node.get("field", "")
            if field:
                if field in _REJECTED_PROVIDER_FIELDS:
                    errors.append(
                        f"Feature '{fname}': price field '{field}' is a raw provider "
                        f"field name. Use normalized warehouse column names: "
                        f"{sorted(VALID_PRICE_BAR_FIELDS)}"
                    )
                elif field not in VALID_PRICE_BAR_FIELDS:
                    errors.append(
                        f"Feature '{fname}': unknown price_bar field '{field}'. "
                        f"Valid fields: {sorted(VALID_PRICE_BAR_FIELDS)}"
                    )

    # Validate dollar-volume field references (close_field / volume_field)
    for fkey in ("close_field", "volume_field"):
        if fkey in node:
            f = node[fkey]
            if f in _REJECTED_PROVIDER_FIELDS:
                errors.append(
                    f"Feature '{fname}': '{fkey}={f}' is a raw provider field name"
                )
            elif f not in VALID_PRICE_BAR_FIELDS:
                errors.append(
                    f"Feature '{fname}': unknown price_bar column '{f}' in '{fkey}'"
                )

    # Validate lookback parameters are positive
    for lkey in _POSITIVE_LOOKBACK_KEYS:
        if lkey in node:
            val = node[lkey]
            if isinstance(val, (int, float)) and val <= 0:
                errors.append(
                    f"Feature '{fname}': '{lkey}' must be positive, got {val}"
                )

    # Recurse into all nested dicts and lists
    for v in node.values():
        if isinstance(v, dict):
            _collect_errors_recursive(fname, v, known_metrics, errors)
        elif isinstance(v, list):
            for item in v:
                _collect_errors_recursive(fname, item, known_metrics, errors)
