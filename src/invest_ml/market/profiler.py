"""Compute CompanyMarketProfile from price bar history."""

from invest_ml.db.models.profiling import CompanyMarketProfile


def profile_security(
    security_id: str,
    bars: list,
    profile_version: str,
    source: str,
) -> CompanyMarketProfile:
    """Compute market investability metrics from price history.

    TODO: implement median dollar volume, market cap, missing-day ratio.
    """
    raise NotImplementedError("TODO: implement market profiling from price bars")
