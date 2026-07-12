"""Pure period classifier for canonical metric normalization."""

from __future__ import annotations

from datetime import date

from invest_ml.canonical.models import PeriodClassification
from invest_ml.canonical.registry import CanonicalMetricRegistry

_ANNUAL_FP = frozenset(["FY"])
_QUARTER_FP = frozenset(["Q1", "Q2", "Q3"])


class CanonicalPeriodClassifier:
    """Classify a single XBRL fact's period as annual, quarter, or unsupported.

    Rules:
    - Duration annual: form in annual_forms, fiscal_period == FY,
      duration in [annual_min, annual_max] days.
    - Duration quarter: form in quarterly_forms, fiscal_period in {Q1,Q2,Q3},
      duration in [quarterly_min, quarterly_max] days.
      Q4 is never a standalone quarter; FY 10-K captures the annual total.
    - Instant annual: form in annual_forms, fiscal_period == FY.
    - Instant quarter: form in quarterly_forms, fiscal_period in {Q1,Q2,Q3}.
    """

    def __init__(self, registry: CanonicalMetricRegistry) -> None:
        self._reg = registry

    def classify(
        self,
        *,
        metric_period_kind: str,
        period_start: date | None,
        period_end: date,
        form: str | None,
        fiscal_period: str | None,
    ) -> PeriodClassification:
        if metric_period_kind == "instant":
            return self._classify_instant(form=form, fiscal_period=fiscal_period)
        if metric_period_kind == "duration":
            return self._classify_duration(
                period_start=period_start,
                period_end=period_end,
                form=form,
                fiscal_period=fiscal_period,
            )
        return PeriodClassification(
            period_type="unsupported",
            supported=False,
            reason=f"unknown metric_period_kind: {metric_period_kind!r}",
            duration_days=None,
        )

    def _classify_instant(
        self,
        *,
        form: str | None,
        fiscal_period: str | None,
    ) -> PeriodClassification:
        if form in self._reg.annual_forms and fiscal_period in _ANNUAL_FP:
            return PeriodClassification(
                period_type="annual",
                supported=True,
                reason="instant/annual",
                duration_days=None,
            )
        if form in self._reg.quarterly_forms and fiscal_period in _QUARTER_FP:
            return PeriodClassification(
                period_type="quarter",
                supported=True,
                reason="instant/quarter",
                duration_days=None,
            )
        return PeriodClassification(
            period_type="unsupported",
            supported=False,
            reason=f"instant: form={form!r} fiscal_period={fiscal_period!r} not in allowed set",
            duration_days=None,
        )

    def _classify_duration(
        self,
        *,
        period_start: date | None,
        period_end: date,
        form: str | None,
        fiscal_period: str | None,
    ) -> PeriodClassification:
        if period_start is None:
            return PeriodClassification(
                period_type="unsupported",
                supported=False,
                reason="duration metric has no period_start",
                duration_days=None,
            )

        duration_days = (period_end - period_start).days

        if form in self._reg.annual_forms:
            if fiscal_period not in _ANNUAL_FP:
                return PeriodClassification(
                    period_type="unsupported",
                    supported=False,
                    reason=f"annual form but fiscal_period={fiscal_period!r} (expected FY)",
                    duration_days=duration_days,
                )
            if self._reg.annual_duration_min <= duration_days <= self._reg.annual_duration_max:
                return PeriodClassification(
                    period_type="annual",
                    supported=True,
                    reason="duration/annual",
                    duration_days=duration_days,
                )
            return PeriodClassification(
                period_type="unsupported",
                supported=False,
                reason=(
                    f"annual form FY but duration {duration_days}d outside "
                    f"[{self._reg.annual_duration_min}, {self._reg.annual_duration_max}]"
                ),
                duration_days=duration_days,
            )

        if form in self._reg.quarterly_forms:
            if fiscal_period not in _QUARTER_FP:
                return PeriodClassification(
                    period_type="unsupported",
                    supported=False,
                    reason=(
                        f"quarterly form but fiscal_period={fiscal_period!r} "
                        "(Q4 and YTD cumulative periods are not classified)"
                    ),
                    duration_days=duration_days,
                )
            if self._reg.quarterly_duration_min <= duration_days <= self._reg.quarterly_duration_max:
                return PeriodClassification(
                    period_type="quarter",
                    supported=True,
                    reason="duration/quarter",
                    duration_days=duration_days,
                )
            return PeriodClassification(
                period_type="unsupported",
                supported=False,
                reason=(
                    f"quarterly form {fiscal_period} but duration {duration_days}d outside "
                    f"[{self._reg.quarterly_duration_min}, {self._reg.quarterly_duration_max}]"
                ),
                duration_days=duration_days,
            )

        return PeriodClassification(
            period_type="unsupported",
            supported=False,
            reason=f"form={form!r} not in any allowed form set",
            duration_days=duration_days,
        )
