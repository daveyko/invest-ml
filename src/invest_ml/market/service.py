"""CompanyMarketProfileService — orchestrates profiling for the candidate universe."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import sessionmaker

from invest_ml.db.repositories.company_market_profiles import CompanyMarketProfileRepository
from invest_ml.market.errors import (
    MarketDataAuthenticationError,
    MarketDataInstrumentNotFoundError,
    MarketDataTemporaryError,
)
from invest_ml.market.models import EquityInstrument
from invest_ml.market.profile import (
    CalculatedMarketProfile,
    MarketProfileCalculationConfig,
    MarketProfileCalculator,
)
from invest_ml.market.provider import EquityPriceProvider

logger = logging.getLogger(__name__)


@dataclass
class CompanyMarketProfileResult:
    targets_found: int
    profiles_succeeded: int
    profiles_not_found: int
    profiles_temporary_failure: int
    metadata_requests: int = 0
    price_requests: int = 0


@dataclass
class MarketProfileRunConfig:
    universe_name: str
    universe_version: str
    profile_version: str
    history_lookback_years: int = 3
    refresh_after_days: int = 30
    failed_symbol_retry_after_days: int = 30
    liquidity_lookback_sessions: int = 90
    missing_ratio_lookback_years: int = 3
    maximum_symbols_per_run: int = 2500


class CompanyMarketProfileService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        price_provider: EquityPriceProvider,
        calculator: MarketProfileCalculator | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._price_provider = price_provider
        self._calculator = calculator or MarketProfileCalculator()

    def materialize(
        self,
        *,
        as_of_date: date,
        config: MarketProfileRunConfig,
    ) -> CompanyMarketProfileResult:
        calc_config = MarketProfileCalculationConfig(
            liquidity_lookback_sessions=config.liquidity_lookback_sessions,
            missing_ratio_lookback_years=config.missing_ratio_lookback_years,
            history_lookback_years=config.history_lookback_years,
        )

        with self._session_factory() as session:
            repo = CompanyMarketProfileRepository(session)
            targets = repo.list_market_profile_targets(
                universe_name=config.universe_name,
                universe_version=config.universe_version,
                profile_version=config.profile_version,
                refresh_after_days=config.refresh_after_days,
                failed_symbol_retry_after_days=config.failed_symbol_retry_after_days,
                maximum_symbols=config.maximum_symbols_per_run,
            )

        logger.info("Found %d market profile targets", len(targets))

        result = CompanyMarketProfileResult(
            targets_found=len(targets),
            profiles_succeeded=0,
            profiles_not_found=0,
            profiles_temporary_failure=0,
        )

        bar_start = as_of_date - timedelta(
            days=int(config.history_lookback_years * 365.2425)
        )

        for target in targets:
            instrument = EquityInstrument(
                security_id=target.security_id,
                company_id=target.company_id,
                ticker=target.ticker,
                exchange=target.exchange,
            )
            scanned_at = datetime.now(tz=UTC)
            self._profile_one(
                instrument=instrument,
                as_of_date=as_of_date,
                bar_start=bar_start,
                calc_config=calc_config,
                profile_version=config.profile_version,
                result=result,
                scanned_at=scanned_at,
            )

        logger.info(
            "company_market_profiles: succeeded=%d not_found=%d temp_failure=%d "
            "metadata_requests=%d price_requests=%d",
            result.profiles_succeeded,
            result.profiles_not_found,
            result.profiles_temporary_failure,
            result.metadata_requests,
            result.price_requests,
        )
        return result

    def _profile_one(
        self,
        *,
        instrument: EquityInstrument,
        as_of_date: date,
        bar_start: date,
        calc_config: MarketProfileCalculationConfig,
        profile_version: str,
        result: CompanyMarketProfileResult,
        scanned_at: datetime,
    ) -> None:
        quality_flags: dict = {}

        try:
            history = self._price_provider.fetch_daily_bars(
                instrument,
                start_date=bar_start,
                end_date=as_of_date,
            )
            result.metadata_requests += 1
            result.price_requests += 1
        except MarketDataInstrumentNotFoundError:
            quality_flags = {"status": "instrument_not_found"}
            result.profiles_not_found += 1
            self._persist(
                instrument=instrument,
                profile_version=profile_version,
                scanned_at=scanned_at,
                quality_flags=quality_flags,
                profile=None,
            )
            return
        except MarketDataTemporaryError as exc:
            logger.warning("Temporary error for %s: %s", instrument.ticker, exc)
            quality_flags = {"status": "temporary_failure", "error": str(exc)}
            result.profiles_temporary_failure += 1
            self._persist(
                instrument=instrument,
                profile_version=profile_version,
                scanned_at=scanned_at,
                quality_flags=quality_flags,
                profile=None,
            )
            return
        except MarketDataAuthenticationError:
            raise

        profile = self._calculator.calculate(
            history,
            as_of_date=as_of_date,
            config=calc_config,
        )

        if profile.status == "no_usable_bars":
            result.profiles_not_found += 1
        else:
            result.profiles_succeeded += 1

        self._persist(
            instrument=instrument,
            profile_version=profile_version,
            scanned_at=scanned_at,
            quality_flags=profile.quality_flags,
            profile=profile,
        )

    def _persist(
        self,
        *,
        instrument: EquityInstrument,
        profile_version: str,
        scanned_at: datetime,
        quality_flags: dict,
        profile: CalculatedMarketProfile | None,
    ) -> None:
        with self._session_factory() as session:
            repo = CompanyMarketProfileRepository(session)
            repo.upsert_profile(
                security_id=instrument.security_id,
                profile_version=profile_version,
                scanned_at=scanned_at,
                source=self._price_provider.adapter_version,
                first_price_date=profile.first_price_date if profile else None,
                latest_price_date=profile.latest_price_date if profile else None,
                price_history_years=profile.price_history_years if profile else None,
                median_daily_dollar_volume=profile.median_daily_dollar_volume if profile else None,
                current_market_cap=None,
                missing_trading_day_ratio=profile.missing_trading_day_ratio if profile else None,
                latest_adjusted_close=profile.latest_adjusted_close if profile else None,
                quality_flags=quality_flags,
            )
            session.commit()
