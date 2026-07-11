"""Market data client interface.

No external API calls are made at import time.
"""

from datetime import date

from invest_ml.db.models.market import PriceBar


class MarketDataClient:
    """Retrieves daily price bars from a market data provider.

    TODO: decide on a provider (e.g. yfinance, Polygon, Tiingo) and implement.
    """

    def fetch_price_bars(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        source: str,
    ) -> list[PriceBar]:
        raise NotImplementedError("TODO: implement price bar fetching")
