"""Parse CompanyFacts JSON into flat XbrlFact records.

Only called for companies that have passed universe selection.
"""

from invest_ml.db.models.financials import XbrlFact


def parse_company_facts(
    company_id: str,
    raw_version_id: str,
    facts_json: dict,
) -> list[XbrlFact]:
    """Flatten a CompanyFacts JSON structure into individual XbrlFact rows.

    TODO: implement tag/unit/period iteration and deterministic fact_id assignment.
    """
    raise NotImplementedError("TODO: implement CompanyFacts → XbrlFact flattening")
