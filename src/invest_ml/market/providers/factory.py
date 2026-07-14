"""Provider factory functions.

Adding a new provider requires only implementing the adapter and registering
it here — no changes to profile logic, models, service, or repository.
"""

from __future__ import annotations

from invest_ml.market.provider import EquityPriceProvider


def create_price_provider(
    provider_name: str,
    api_token: str,
    base_url: str,
    symbol_overrides: dict[str, str] | None = None,
) -> EquityPriceProvider:
    if provider_name == "tiingo":
        from invest_ml.market.providers.tiingo.eod_provider import (
            TiingoEodProvider,
            TiingoEodSettings,
        )

        settings = TiingoEodSettings(api_token=api_token, base_url=base_url)
        return TiingoEodProvider(settings, symbol_overrides=symbol_overrides)

    raise ValueError(f"Unknown market data provider: {provider_name!r}")
