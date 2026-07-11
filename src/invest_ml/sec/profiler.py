"""Scan CompanyFacts JSON and produce a lightweight data profile.

Intentionally narrow: counts periods, detects the presence of the 7
canonical profiling metrics, and summarises data quality.  The raw JSON
is NEVER persisted during this broad scan.  Heavy persistence is reserved
for universe members only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import orjson

logger = logging.getLogger(__name__)

_QUARTERLY_FPS = frozenset({"Q1", "Q2", "Q3", "Q4"})

# Path resolution: src/invest_ml/sec/ → three levels up → project root.
_DEFAULT_CONFIG_PATH = (
    Path(__file__).parent.parent.parent.parent / "configs" / "canonical_metrics_v1.yaml"
)


# ── Config dataclasses ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConceptSpec:
    taxonomy: str
    tag: str


@dataclass(frozen=True)
class ProfilingMetricSpec:
    name: str
    period_kind: str  # "duration" or "instant"
    units: frozenset
    concepts: tuple


@dataclass(frozen=True)
class ProfilingConfig:
    required_metrics: tuple       # ordered list of 7 metric names
    annual_forms: frozenset
    quarterly_forms: frozenset
    metrics: dict                 # metric_name -> ProfilingMetricSpec

    @classmethod
    def from_canonical_metrics(cls, cfg: dict) -> ProfilingConfig:
        """Parse the ``profiling`` section of the canonical_metrics YAML dict."""
        import yaml  # noqa: F401 — yaml is declared in project dependencies

        profiling = cfg["profiling"]
        metrics: dict[str, ProfilingMetricSpec] = {}
        for name, spec in profiling["metrics"].items():
            metrics[name] = ProfilingMetricSpec(
                name=name,
                period_kind=spec["period_kind"],
                units=frozenset(spec["units"]),
                concepts=tuple(
                    ConceptSpec(c["taxonomy"], c["tag"])
                    for c in spec["concepts"]
                ),
            )
        return cls(
            required_metrics=tuple(profiling["required_metrics"]),
            annual_forms=frozenset(profiling["annual_forms"]),
            quarterly_forms=frozenset(profiling["quarterly_forms"]),
            metrics=metrics,
        )

    @classmethod
    def from_yaml(cls, path: Path = _DEFAULT_CONFIG_PATH) -> ProfilingConfig:
        import yaml

        with path.open() as fh:
            cfg = yaml.safe_load(fh)
        return cls.from_canonical_metrics(cfg)


# ── Result dataclass ─────────────────────────────────────────────────────────


@dataclass
class CompanyDataProfileResult:
    """Profile row ready for upsert into company_data_profiles."""

    company_id: UUID
    profile_version: str
    scanned_at: datetime
    source_run_id: UUID | None
    first_period_end: date | None
    latest_period_end: date | None
    latest_filed_date: date | None
    annual_periods: int
    quarterly_periods: int
    has_revenue: bool
    has_operating_income: bool
    has_net_income: bool
    has_operating_cash_flow: bool
    has_cash: bool
    has_debt: bool
    has_shares: bool
    canonical_metric_coverage: float
    fact_count: int
    quality_flags: dict


# ── Profiler ─────────────────────────────────────────────────────────────────


class CompanyFactsProfiler:
    """Compute a CompanyDataProfileResult from a raw CompanyFacts JSON payload."""

    def __init__(self, config: ProfilingConfig) -> None:
        self._config = config
        # Build lookup: (taxonomy, tag, unit) -> set of metric names
        self._concept_index: dict[tuple, set] = {}
        for metric_name, spec in config.metrics.items():
            for concept in spec.concepts:
                for unit in spec.units:
                    key = (concept.taxonomy, concept.tag, unit)
                    self._concept_index.setdefault(key, set()).add(metric_name)

        # Set of known taxonomies (for unsupported_taxonomies quality flag)
        self._known_taxonomies: frozenset = frozenset(
            c.taxonomy
            for spec in config.metrics.values()
            for c in spec.concepts
        )

    def profile(
        self,
        company_id: UUID,
        cik: str,
        payload: bytes,
        *,
        profile_version: str,
        scanned_at: datetime,
        source_run_id: UUID | None,
        cik_mismatch: bool = False,
    ) -> CompanyDataProfileResult:
        """Parse *payload* and return an unsaved profile row."""
        try:
            data = orjson.loads(payload)
        except Exception as exc:
            logger.warning("CIK %s: JSON parse error: %s", cik, exc)
            return self._empty_result(
                company_id, cik, profile_version, scanned_at, source_run_id,
                quality_flags={"parse_error": str(exc), "cik_mismatch": cik_mismatch},
            )

        facts = data.get("facts", {})
        if not isinstance(facts, dict):
            return self._empty_result(
                company_id, cik, profile_version, scanned_at, source_run_id,
                quality_flags={"malformed_facts": True, "cik_mismatch": cik_mismatch},
            )

        found_metrics: set = set()
        annual_keys: set = set()      # (fy, end_date)
        quarterly_keys: set = set()   # (fy, fp, end_date)
        period_ends: list = []
        filed_dates: list = []
        total_fact_count = 0
        malformed_count = 0
        unsupported_taxonomies: set = set()

        for taxonomy, tags in facts.items():
            if not isinstance(tags, dict):
                continue
            if taxonomy not in self._known_taxonomies:
                unsupported_taxonomies.add(taxonomy)
            for tag, tag_data in tags.items():
                if not isinstance(tag_data, dict):
                    continue
                for unit_type, observations in tag_data.get("units", {}).items():
                    if not isinstance(observations, list):
                        continue
                    for obs in observations:
                        if not isinstance(obs, dict):
                            malformed_count += 1
                            continue

                        end_str = obs.get("end")
                        if not end_str:
                            malformed_count += 1
                            continue
                        try:
                            end_date = date.fromisoformat(end_str)
                        except (ValueError, TypeError):
                            malformed_count += 1
                            continue

                        if "val" not in obs:
                            malformed_count += 1
                            continue

                        total_fact_count += 1
                        period_ends.append(end_date)

                        filed_str = obs.get("filed")
                        if filed_str:
                            try:
                                filed_dates.append(date.fromisoformat(filed_str))
                            except (ValueError, TypeError):
                                pass

                        # Check metric coverage (val may be 0 but must exist).
                        if obs.get("val") is not None:
                            key = (taxonomy, tag, unit_type)
                            if key in self._concept_index:
                                found_metrics.update(self._concept_index[key])

                        # Period counting.
                        fp = obs.get("fp") or ""
                        form = obs.get("form") or ""
                        fy = obs.get("fy")

                        if (
                            form in self._config.annual_forms
                            and fp == "FY"
                            and fy is not None
                        ):
                            annual_keys.add((fy, end_date))
                        elif (
                            form in self._config.quarterly_forms
                            and fp in _QUARTERLY_FPS
                            and fy is not None
                        ):
                            quarterly_keys.add((fy, fp, end_date))

        required = self._config.required_metrics
        available = sum(1 for m in required if m in found_metrics)
        coverage = round(available / len(required), 6) if required else 0.0

        missing_metrics = [m for m in required if m not in found_metrics]
        quality_flags: dict = {
            "missing_metrics": missing_metrics,
            "malformed_fact_count": malformed_count,
            "unsupported_taxonomies": sorted(unsupported_taxonomies),
            "cik_mismatch": cik_mismatch,
            "companyfacts_member_missing": False,
        }

        return CompanyDataProfileResult(
            company_id=company_id,
            profile_version=profile_version,
            scanned_at=scanned_at,
            source_run_id=source_run_id,
            first_period_end=min(period_ends) if period_ends else None,
            latest_period_end=max(period_ends) if period_ends else None,
            latest_filed_date=max(filed_dates) if filed_dates else None,
            annual_periods=len(annual_keys),
            quarterly_periods=len(quarterly_keys),
            has_revenue="revenue" in found_metrics,
            has_operating_income="operating_income" in found_metrics,
            has_net_income="net_income" in found_metrics,
            has_operating_cash_flow="operating_cash_flow" in found_metrics,
            has_cash="cash" in found_metrics,
            has_debt="debt" in found_metrics,
            has_shares="shares" in found_metrics,
            canonical_metric_coverage=coverage,
            fact_count=total_fact_count,
            quality_flags=quality_flags,
        )

    def profile_missing(
        self,
        company_id: UUID,
        cik: str,
        *,
        profile_version: str,
        scanned_at: datetime,
        source_run_id: UUID | None,
    ) -> CompanyDataProfileResult:
        """Return a zero-coverage profile for a company absent from the archive."""
        return self._empty_result(
            company_id, cik, profile_version, scanned_at, source_run_id,
            quality_flags={"companyfacts_member_missing": True},
        )

    # ── Private ──────────────────────────────────────────────────────────────

    def _empty_result(
        self,
        company_id: UUID,
        cik: str,
        profile_version: str,
        scanned_at: datetime,
        source_run_id: UUID | None,
        quality_flags: dict,
    ) -> CompanyDataProfileResult:
        return CompanyDataProfileResult(
            company_id=company_id,
            profile_version=profile_version,
            scanned_at=scanned_at,
            source_run_id=source_run_id,
            first_period_end=None,
            latest_period_end=None,
            latest_filed_date=None,
            annual_periods=0,
            quarterly_periods=0,
            has_revenue=False,
            has_operating_income=False,
            has_net_income=False,
            has_operating_cash_flow=False,
            has_cash=False,
            has_debt=False,
            has_shares=False,
            canonical_metric_coverage=0.0,
            fact_count=0,
            quality_flags=quality_flags,
        )
