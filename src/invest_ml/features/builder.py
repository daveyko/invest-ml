"""Compute feature snapshots for a company/security/as-of-date.

Domain rules encoded here:
  - All financial inputs must satisfy available_at <= as_of_date (enforced via
    assert_no_lookahead before any data is used).
  - Snapshots are immutable.  If upstream data changes, call build_snapshot
    again; a new snapshot will be inserted with a new source_lineage_hash.
"""

from datetime import date
from uuid import UUID

from invest_ml.db.models.features import FeatureSnapshot
from invest_ml.utils import assert_no_lookahead, source_lineage_hash


def build_snapshot(
    company_id: UUID,
    security_id: UUID,
    as_of_date: date,
    feature_set_id: UUID,
    canonical_metrics: list,
    price_bars: list,
    features_config: dict,
    dagster_run_id: str | None = None,
) -> FeatureSnapshot:
    """Compute an immutable feature snapshot for one company on one date.

    TODO: implement per-feature calculation using assert_no_lookahead on each input.
    """
    raise NotImplementedError("TODO: implement feature calculation")
