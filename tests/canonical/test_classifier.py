"""Tests for CanonicalPeriodClassifier."""

from datetime import date

import pytest

from invest_ml.canonical.classifier import CanonicalPeriodClassifier
from invest_ml.canonical.registry import CanonicalMetricRegistry

_CFG = {
    "version": "canonical_metrics_v1",
    "defaults": {
        "annual_forms": ["10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"],
        "quarterly_forms": ["10-Q", "10-Q/A"],
        "annual_duration_days": {"min": 300, "max": 430},
        "quarterly_duration_days": {"min": 60, "max": 120},
    },
    "metrics": {
        "revenue": {
            "period_kind": "duration",
            "expected_units": ["USD"],
            "concepts": [{"taxonomy": "us-gaap", "tag": "Revenues", "priority": 1}],
        },
        "total_assets": {
            "period_kind": "instant",
            "expected_units": ["USD"],
            "concepts": [{"taxonomy": "us-gaap", "tag": "Assets", "priority": 1}],
        },
    },
}


@pytest.fixture
def classifier():
    registry = CanonicalMetricRegistry.from_config(_CFG)
    return CanonicalPeriodClassifier(registry)


# ── Duration annual ───────────────────────────────────────────────────────────

def test_duration_annual_10k_fy(classifier):
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
        form="10-K",
        fiscal_period="FY",
    )
    assert result.supported
    assert result.period_type == "annual"
    assert result.duration_days == 364


def test_duration_annual_20f_fy(classifier):
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 4, 1),
        period_end=date(2024, 3, 31),
        form="20-F",
        fiscal_period="FY",
    )
    assert result.supported
    assert result.period_type == "annual"


def test_duration_annual_amendment(classifier):
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
        form="10-K/A",
        fiscal_period="FY",
    )
    assert result.supported
    assert result.period_type == "annual"


def test_duration_annual_too_short(classifier):
    # 299 days < 300 min
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 3, 8),
        period_end=date(2023, 12, 31),
        form="10-K",
        fiscal_period="FY",
    )
    assert not result.supported
    assert "outside" in result.reason


def test_duration_annual_too_long(classifier):
    # 431 days > 430 max: date(2024, 3, 7) - date(2023, 1, 1) = 431 days
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 1, 1),
        period_end=date(2024, 3, 7),
        form="10-K",
        fiscal_period="FY",
    )
    assert not result.supported


# ── Duration quarter ──────────────────────────────────────────────────────────

def test_duration_quarter_10q_q1(classifier):
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 3, 31),
        form="10-Q",
        fiscal_period="Q1",
    )
    assert result.supported
    assert result.period_type == "quarter"
    assert result.duration_days == 89


def test_duration_quarter_10q_q2(classifier):
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 4, 1),
        period_end=date(2023, 6, 30),
        form="10-Q",
        fiscal_period="Q2",
    )
    assert result.supported
    assert result.period_type == "quarter"


def test_duration_q4_not_classified(classifier):
    """Q4 is never a standalone quarter; 10-K FY captures the full year."""
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 10, 1),
        period_end=date(2023, 12, 31),
        form="10-Q",
        fiscal_period="Q4",
    )
    assert not result.supported
    assert "Q4" in result.reason or "not classified" in result.reason


def test_duration_ytd_not_classified(classifier):
    """YTD cumulative (e.g. 9-month period for Q3) is not classified."""
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 9, 30),
        form="10-Q",
        fiscal_period="Q3",
    )
    assert not result.supported  # 272 days > 120 max quarterly


def test_duration_no_period_start(classifier):
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=None,
        period_end=date(2023, 12, 31),
        form="10-K",
        fiscal_period="FY",
    )
    assert not result.supported
    assert "period_start" in result.reason


def test_duration_unknown_form(classifier):
    result = classifier.classify(
        metric_period_kind="duration",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
        form="S-1",
        fiscal_period="FY",
    )
    assert not result.supported


# ── Instant ───────────────────────────────────────────────────────────────────

def test_instant_annual_10k_fy(classifier):
    result = classifier.classify(
        metric_period_kind="instant",
        period_start=None,
        period_end=date(2023, 12, 31),
        form="10-K",
        fiscal_period="FY",
    )
    assert result.supported
    assert result.period_type == "annual"
    assert result.duration_days is None


def test_instant_quarter_10q_q1(classifier):
    result = classifier.classify(
        metric_period_kind="instant",
        period_start=None,
        period_end=date(2023, 3, 31),
        form="10-Q",
        fiscal_period="Q1",
    )
    assert result.supported
    assert result.period_type == "quarter"


def test_instant_q4_not_classified(classifier):
    result = classifier.classify(
        metric_period_kind="instant",
        period_start=None,
        period_end=date(2023, 12, 31),
        form="10-Q",
        fiscal_period="Q4",
    )
    assert not result.supported


def test_instant_unknown_form(classifier):
    result = classifier.classify(
        metric_period_kind="instant",
        period_start=None,
        period_end=date(2023, 12, 31),
        form="8-K",
        fiscal_period="FY",
    )
    assert not result.supported


def test_instant_none_form(classifier):
    result = classifier.classify(
        metric_period_kind="instant",
        period_start=None,
        period_end=date(2023, 12, 31),
        form=None,
        fiscal_period="FY",
    )
    assert not result.supported


# ── Unknown period_kind ───────────────────────────────────────────────────────

def test_unknown_period_kind(classifier):
    result = classifier.classify(
        metric_period_kind="ttm",
        period_start=date(2023, 1, 1),
        period_end=date(2023, 12, 31),
        form="10-K",
        fiscal_period="FY",
    )
    assert not result.supported
    assert "unknown" in result.reason
