"""Symbol resolution with optional override mapping."""

from __future__ import annotations

from invest_ml.market.models import EquityInstrument


class SymbolResolver:
    """Resolve a canonical ticker to a Tiingo provider ticker.

    symbol_overrides maps canonical_ticker → tiingo_ticker.
    """

    def __init__(self, symbol_overrides: dict[str, str] | None = None) -> None:
        self._overrides: dict[str, str] = symbol_overrides or {}

    def resolve(self, instrument: EquityInstrument) -> str:
        return self._overrides.get(instrument.ticker, instrument.ticker)
