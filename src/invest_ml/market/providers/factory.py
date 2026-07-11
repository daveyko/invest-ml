"""Provider factory functions.

Adding a new provider requires only implementing the adapter and registering
it here — no changes to profile logic, models, service, or repository.
"""

from __future__ import annotations

from invest_ml.market.provider import EquityPriceProvider, MarketCapitalizationProvider


def create_price_provider(
    provider_name: str,
    api_token: str,
    base_url: str,
    fundamentals_enabled: bool = False,
    symbol_overrides: dict[str, str] | None = None,
) -> EquityPriceProvider:
    if provider_name == "tiingo":
        from invest_ml.market.providers.tiingo.eod_provider import (
            TiingoEodProvider,
            TiingoEodSettings,
        )

        settings = TiingoEodSettings(
            api_token=api_token,
            base_url=base_url,
            fundamentals_enabled=fundamentals_enabled,
        )
        return TiingoEodProvider(settings, symbol_overrides=symbol_overrides)

    raise ValueError(f"Unknown market data provider: {provider_name!r}")


def create_market_cap_provider(
    provider_name: str,
    api_token: str,
    base_url: str,
    market_cap_lookback_days: int = 10,
    symbol_overrides: dict[str, str] | None = None,
) -> MarketCapitalizationProvider:
    if provider_name == "tiingo":
        from invest_ml.market.providers.tiingo.fundamentals_provider import (
            TiingoFundamentalsProvider,
            TiingoFundamentalsSettings,
        )

        settings = TiingoFundamentalsSettings(
            api_token=api_token,
            base_url=base_url,
            market_cap_lookback_days=market_cap_lookback_days,
        )
        return TiingoFundamentalsProvider(settings, symbol_overrides=symbol_overrides)

    raise ValueError(f"Unknown market cap provider: {provider_name!r}")
