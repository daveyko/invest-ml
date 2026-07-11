"""Build and hash feature snapshot source lineage records."""

from datetime import date
from uuid import UUID

from invest_ml.utils import source_lineage_hash


def build_lineage(
    metric_ids: list[str],
    price_bar_keys: list[tuple[str, date]],
    feature_set_id: UUID,
    normalization_version: str,
) -> dict:
    """Return a canonical lineage dict that identifies every input used.

    If any input changes, the resulting hash will differ, triggering a new snapshot.
    """
    lineage = {
        "canonical_metric_ids": sorted(str(m) for m in metric_ids),
        "price_bar_keys": sorted(f"{sid}:{d.isoformat()}" for sid, d in price_bar_keys),
        "feature_set_id": str(feature_set_id),
        "normalization_version": normalization_version,
    }
    return {**lineage, "_hash": source_lineage_hash(lineage)}
