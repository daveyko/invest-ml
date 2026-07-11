"""Universe construction logic.

Filtering is based on data quality and market investability, NOT on
hindsight judgments about business quality.  The four layers are:

  All companies        SEC submissions: lightweight metadata for all CIKs
  Candidate universe   US-listed, operating, recently filed
  Training universe    Broad: sufficient financial + market history
  Scoring universe     Narrow: AI/crypto/software/semiconductor/fintech/etc.
"""

from invest_ml.db.models.universe import UniverseDefinition, UniverseMembership


def build_candidate_universe(
    universe_config: dict,
    sic_config: dict,
    universe_version: str,
) -> tuple[UniverseDefinition, list[UniverseMembership]]:
    """Apply candidate filter criteria to the company catalog.

    TODO: query companies + securities, apply exchange/ticker/filing-recency filters.
    """
    raise NotImplementedError("TODO: implement candidate universe construction")


def build_training_universe(
    candidate_membership: list[UniverseMembership],
    universe_config: dict,
    universe_version: str,
) -> tuple[UniverseDefinition, list[UniverseMembership]]:
    """Apply training filters: min annual periods, price history, coverage, dollar volume.

    TODO: join data profiles + market profiles, apply thresholds.
    """
    raise NotImplementedError("TODO: implement training universe construction")


def build_scoring_universe(
    candidate_membership: list[UniverseMembership],
    universe_config: dict,
    sic_config: dict,
    universe_version: str,
) -> tuple[UniverseDefinition, list[UniverseMembership]]:
    """Filter candidate to model_buckets + always_include tickers.

    TODO: filter by model_bucket classification or always_include ticker list.
    """
    raise NotImplementedError("TODO: implement scoring universe construction")
