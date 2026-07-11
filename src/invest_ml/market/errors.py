"""Provider-agnostic market data error hierarchy."""


class MarketDataError(Exception):
    pass


class MarketDataAuthenticationError(MarketDataError):
    pass


class MarketDataRateLimitError(MarketDataError):
    pass


class MarketDataQuotaExhaustedError(MarketDataError):
    pass


class MarketDataInstrumentNotFoundError(MarketDataError):
    pass


class MarketDataInvalidResponseError(MarketDataError):
    pass


class MarketDataTemporaryError(MarketDataError):
    pass


class MarketDataUnsupportedCapabilityError(MarketDataError):
    pass


class MarketDataEntitlementError(MarketDataError):
    pass
