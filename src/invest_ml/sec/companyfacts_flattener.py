"""Flatten SEC CompanyFacts JSON into FlattenedXbrlFact records.

Only observations whose (taxonomy, tag) pair appears in the fact registry
are kept.  The registry is derived from canonical_metrics_v1.yaml and its
SHA-256 hash is embedded in the derivation version so that registry changes
force re-derivation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from invest_ml.xbrl.models import FlattenedXbrlFact

logger = logging.getLogger(__name__)

# Fields that belong to the SEC observation envelope, not dimensions.
_STANDARD_OBS_FIELDS = frozenset({"end", "filed", "val", "start", "accn", "fy", "fp", "form", "frame"})

_FLATTENER_BASE_VERSION = "companyfacts_flattener_v1"


def build_fact_registry(canonical_metrics_config: dict) -> dict[tuple[str, str], str]:
    """Build {(taxonomy, tag): metric_name} registry from canonical_metrics config.

    Includes every concept listed under each metric's 'concepts' key.
    First mapping wins when the same (taxonomy, tag) appears in multiple metrics.
    """
    registry: dict[tuple[str, str], str] = {}
    for metric_name, metric_def in canonical_metrics_config.get("metrics", {}).items():
        for concept in metric_def.get("concepts", []):
            taxonomy = str(concept.get("taxonomy", ""))
            tag = str(concept.get("tag", ""))
            if taxonomy and tag:
                key = (taxonomy, tag)
                if key not in registry:
                    registry[key] = metric_name
    return registry


def _registry_hash(registry: dict[tuple[str, str], str]) -> str:
    entries = sorted(f"{tax}:{tag}" for tax, tag in registry.keys())
    canonical = json.dumps(entries, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fact_id(
    company_id: UUID,
    taxonomy: str,
    tag: str,
    unit: str,
    period_start: date | None,
    period_end: date,
    value: Decimal,
    accession_number: str | None,
    fiscal_year: int | None,
    fiscal_period: str | None,
    form: str | None,
    filed_date: date,
    frame: str | None,
    dimensions: dict,
) -> str:
    parts = {
        "company_id": str(company_id),
        "taxonomy": taxonomy,
        "tag": tag,
        "unit": unit,
        "period_start": str(period_start) if period_start else "",
        "period_end": str(period_end),
        "value": str(value),
        "accession_number": accession_number or "",
        "fiscal_year": str(fiscal_year) if fiscal_year is not None else "",
        "fiscal_period": fiscal_period or "",
        "form": form or "",
        "filed_date": str(filed_date),
        "frame": frame or "",
        "dimensions": json.dumps(dimensions, sort_keys=True, separators=(",", ":")),
    }
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class CompanyFactsFlattener:
    """Flatten one CompanyFacts payload into FlattenedXbrlFact records.

    Only (taxonomy, tag) pairs present in the registry are emitted.
    Observations with missing required fields or invalid date/value types
    are silently dropped.
    """

    def __init__(self, registry: dict[tuple[str, str], str]) -> None:
        self._registry = registry
        self._reg_hash = _registry_hash(registry)

    @classmethod
    def from_config(cls, canonical_metrics_config: dict) -> CompanyFactsFlattener:
        return cls(build_fact_registry(canonical_metrics_config))

    @property
    def derivation_version(self) -> str:
        return f"{_FLATTENER_BASE_VERSION}:{self._reg_hash[:16]}"

    def flatten(
        self,
        company_id: UUID,
        raw_version_id: UUID,
        payload: bytes,
    ) -> list[FlattenedXbrlFact]:
        try:
            data: dict[str, Any] = json.loads(payload, parse_float=Decimal)
        except Exception as exc:
            raise ValueError(f"JSON parse failed for company {company_id}: {exc}") from exc

        facts = data.get("facts") or {}

        result: list[FlattenedXbrlFact] = []
        for taxonomy_name, taxonomy_data in facts.items():
            if not isinstance(taxonomy_data, dict):
                continue
            for tag, tag_data in taxonomy_data.items():
                if (taxonomy_name, tag) not in self._registry:
                    continue
                if not isinstance(tag_data, dict):
                    continue
                label: str | None = tag_data.get("label")
                description: str | None = tag_data.get("description")
                units_map = tag_data.get("units") or {}
                if not isinstance(units_map, dict):
                    continue
                for unit, observations in units_map.items():
                    if not isinstance(observations, list):
                        continue
                    for obs in observations:
                        fact = self._process_observation(
                            company_id, raw_version_id,
                            taxonomy_name, tag, label, description, unit, obs,
                        )
                        if fact is not None:
                            result.append(fact)

        return result

    def _process_observation(
        self,
        company_id: UUID,
        raw_version_id: UUID,
        taxonomy: str,
        tag: str,
        label: str | None,
        description: str | None,
        unit: str,
        obs: Any,
    ) -> FlattenedXbrlFact | None:
        if not isinstance(obs, dict):
            return None

        end_str = obs.get("end")
        filed_str = obs.get("filed")
        val = obs.get("val")

        if not end_str or not filed_str or val is None:
            return None

        try:
            period_end = date.fromisoformat(str(end_str))
            filed_date = date.fromisoformat(str(filed_str))
        except (ValueError, TypeError):
            return None

        period_start: date | None = None
        start_str = obs.get("start")
        if start_str:
            try:
                period_start = date.fromisoformat(str(start_str))
            except (ValueError, TypeError):
                return None
            if period_start > period_end:
                return None

        try:
            value = Decimal(str(val)) if not isinstance(val, Decimal) else val
        except (InvalidOperation, TypeError):
            return None

        accession_number: str | None = obs.get("accn") or None
        raw_fy = obs.get("fy")
        fiscal_year: int | None = int(raw_fy) if raw_fy is not None else None
        fiscal_period: str | None = obs.get("fp") or None
        form: str | None = obs.get("form") or None
        frame: str | None = obs.get("frame") or None
        dimensions = {k: v for k, v in obs.items() if k not in _STANDARD_OBS_FIELDS}

        fid = _fact_id(
            company_id=company_id,
            taxonomy=taxonomy,
            tag=tag,
            unit=unit,
            period_start=period_start,
            period_end=period_end,
            value=value,
            accession_number=accession_number,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            form=form,
            filed_date=filed_date,
            frame=frame,
            dimensions=dimensions,
        )

        return FlattenedXbrlFact(
            fact_id=fid,
            company_id=company_id,
            taxonomy=taxonomy,
            tag=tag,
            label=label,
            description=description,
            unit=unit,
            period_start=period_start,
            period_end=period_end,
            value=value,
            accession_number=accession_number,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            form=form,
            filed_date=filed_date,
            frame=frame,
            dimensions=dimensions,
            raw_version_id=raw_version_id,
        )
