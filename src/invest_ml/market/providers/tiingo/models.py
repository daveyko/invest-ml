"""Tiingo-specific Pydantic response models.

These models are ONLY used inside the tiingo adapter package.
Nothing outside market/providers/tiingo/ should import from here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TiingoMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticker: str
    name: str | None = None
    exchangeCode: str | None = None
    startDate: str | None = None
    endDate: str | None = None


class TiingoBarResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: float | None = None
    adjOpen: float | None = None
    adjHigh: float | None = None
    adjLow: float | None = None
    adjClose: float | None = None
    adjVolume: float | None = None
    divCash: float | None = None
    splitFactor: float | None = None


class TiingoFundamentalsRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    marketCap: float | None = None
