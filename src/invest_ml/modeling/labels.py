"""Compute realized labels from price history.

Labels use FUTURE prices relative to as_of_date.  This is intentional and correct.
Feature construction must NEVER use data with available_at > as_of_date.
"""

from datetime import date
from uuid import UUID

from invest_ml.db.models.modeling import Label, TargetSpec


def compute_label(
    company_id: UUID,
    security_id: UUID,
    as_of_date: date,
    target_spec: TargetSpec,
    price_bars: list,
) -> Label | None:
    """Return a Label if the horizon has elapsed, else None.

    TODO: implement price lookup for start/end of horizon, return calculation.
    """
    raise NotImplementedError("TODO: implement label calculation from price history")
