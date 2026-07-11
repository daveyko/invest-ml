"""Normalize raw XBRL facts into canonical metrics using the metrics config."""

from invest_ml.db.models.financials import CanonicalMetric, XbrlFact


def normalize_facts(
    company_id: str,
    facts: list[XbrlFact],
    metrics_config: dict,
    normalization_version: str,
) -> list[CanonicalMetric]:
    """Apply canonical metric definitions to XbrlFacts and return CanonicalMetric rows.

    Point-in-time rule: available_at is set to filed_date of the source fact,
    never to today's date.

    TODO: implement tag selection, TTM aggregation, and period-type handling.
    """
    raise NotImplementedError("TODO: implement canonical metric normalization")
