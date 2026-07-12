"""Load and validate the canonical metrics registry from YAML config."""

from __future__ import annotations

import hashlib
import json

from invest_ml.canonical.models import ConceptConfig, MetricConfig

_VALID_PERIOD_KINDS = frozenset(["duration", "instant"])


class CanonicalMetricRegistry:
    """Immutable registry loaded from canonical_metrics_v1.yaml.

    Provides deterministic configuration_hash so normalization_version
    records embed the exact config that produced each canonical metric row.
    """

    def __init__(
        self,
        *,
        version: str,
        configuration_hash: str,
        metrics: dict[str, MetricConfig],
        annual_forms: frozenset[str],
        quarterly_forms: frozenset[str],
        annual_duration_min: int,
        annual_duration_max: int,
        quarterly_duration_min: int,
        quarterly_duration_max: int,
    ) -> None:
        self.version = version
        self.configuration_hash = configuration_hash
        self.metrics = metrics
        self.annual_forms = annual_forms
        self.quarterly_forms = quarterly_forms
        self.annual_duration_min = annual_duration_min
        self.annual_duration_max = annual_duration_max
        self.quarterly_duration_min = quarterly_duration_min
        self.quarterly_duration_max = quarterly_duration_max

        # (taxonomy, tag) → (metric_name, ConceptConfig); first mapping wins
        self._concept_lookup: dict[tuple[str, str], tuple[str, ConceptConfig]] = {}
        for metric_name, metric_config in metrics.items():
            for concept in metric_config.concepts:
                key = (concept.taxonomy, concept.tag)
                if key not in self._concept_lookup:
                    self._concept_lookup[key] = (metric_name, concept)

    @classmethod
    def from_config(cls, raw: dict) -> CanonicalMetricRegistry:
        version = str(raw.get("version", "canonical_metrics_v1"))
        defaults = raw.get("defaults", {})

        annual_forms = frozenset(defaults.get("annual_forms", []))
        quarterly_forms = frozenset(defaults.get("quarterly_forms", []))
        annual_dur = defaults.get("annual_duration_days", {})
        quarterly_dur = defaults.get("quarterly_duration_days", {})

        annual_duration_min = int(annual_dur.get("min", 300))
        annual_duration_max = int(annual_dur.get("max", 430))
        quarterly_duration_min = int(quarterly_dur.get("min", 60))
        quarterly_duration_max = int(quarterly_dur.get("max", 120))

        metrics: dict[str, MetricConfig] = {}
        for metric_name, metric_def in raw.get("metrics", {}).items():
            period_kind = metric_def.get("period_kind", "")
            if period_kind not in _VALID_PERIOD_KINDS:
                raise ValueError(
                    f"Metric '{metric_name}': invalid period_kind '{period_kind}'"
                )

            expected_units = tuple(metric_def.get("expected_units", []))
            if not expected_units:
                raise ValueError(f"Metric '{metric_name}': expected_units is empty")

            raw_concepts = metric_def.get("concepts", [])
            if not raw_concepts:
                raise ValueError(f"Metric '{metric_name}': concepts list is empty")

            concepts: list[ConceptConfig] = []
            for c in raw_concepts:
                taxonomy = str(c.get("taxonomy", ""))
                tag = str(c.get("tag", ""))
                priority = int(c.get("priority", 0))
                if not taxonomy or not tag:
                    raise ValueError(
                        f"Metric '{metric_name}': concept missing taxonomy or tag: {c}"
                    )
                concepts.append(ConceptConfig(taxonomy=taxonomy, tag=tag, priority=priority))

            priorities = [c.priority for c in concepts]
            if len(priorities) != len(set(priorities)):
                raise ValueError(f"Metric '{metric_name}': duplicate concept priorities")

            metrics[metric_name] = MetricConfig(
                name=metric_name,
                period_kind=period_kind,
                expected_units=expected_units,
                concepts=tuple(sorted(concepts, key=lambda c: c.priority)),
            )

        configuration_hash = _compute_config_hash(
            version=version,
            defaults=defaults,
            metrics=metrics,
        )

        return cls(
            version=version,
            configuration_hash=configuration_hash,
            metrics=metrics,
            annual_forms=annual_forms,
            quarterly_forms=quarterly_forms,
            annual_duration_min=annual_duration_min,
            annual_duration_max=annual_duration_max,
            quarterly_duration_min=quarterly_duration_min,
            quarterly_duration_max=quarterly_duration_max,
        )

    def taxonomy_tags(self) -> list[tuple[str, str]]:
        """All unique (taxonomy, tag) pairs across all metrics."""
        return list(self._concept_lookup.keys())

    def find_concept(
        self, taxonomy: str, tag: str
    ) -> tuple[str, ConceptConfig] | None:
        """Return (metric_name, concept_config) for this taxonomy/tag, or None."""
        return self._concept_lookup.get((taxonomy, tag))


def _compute_config_hash(
    *,
    version: str,
    defaults: dict,
    metrics: dict[str, MetricConfig],
) -> str:
    """SHA-256 over a canonicalized representation of the normalization config."""
    canonical = {
        "version": version,
        "defaults": {
            "annual_forms": sorted(defaults.get("annual_forms", [])),
            "quarterly_forms": sorted(defaults.get("quarterly_forms", [])),
            "annual_duration_days": {
                "min": defaults.get("annual_duration_days", {}).get("min"),
                "max": defaults.get("annual_duration_days", {}).get("max"),
            },
            "quarterly_duration_days": {
                "min": defaults.get("quarterly_duration_days", {}).get("min"),
                "max": defaults.get("quarterly_duration_days", {}).get("max"),
            },
        },
        "metrics": {
            name: {
                "period_kind": m.period_kind,
                "expected_units": sorted(m.expected_units),
                "concepts": [
                    {"taxonomy": c.taxonomy, "tag": c.tag, "priority": c.priority}
                    for c in sorted(m.concepts, key=lambda c: c.priority)
                ],
            }
            for name, m in sorted(metrics.items())
        },
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
