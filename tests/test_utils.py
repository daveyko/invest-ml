"""Test deterministic hashing utilities and point-in-time invariant guards."""

from datetime import date

import pytest


def test_deterministic_hash_is_stable() -> None:
    from invest_ml.utils import deterministic_hash

    data = {"b": 2, "a": 1}
    h1 = deterministic_hash(data)
    h2 = deterministic_hash({"a": 1, "b": 2})  # different insertion order
    assert h1 == h2, "Hash must be key-order independent"


def test_deterministic_hash_differs_on_value_change() -> None:
    from invest_ml.utils import deterministic_hash

    assert deterministic_hash({"x": 1}) != deterministic_hash({"x": 2})


def test_deterministic_hash_string_input() -> None:
    from invest_ml.utils import deterministic_hash

    h = deterministic_hash("hello")
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex


def test_xbrl_fact_id_is_deterministic() -> None:
    from invest_ml.utils import xbrl_fact_id

    kwargs = dict(
        company_id="abc-123",
        taxonomy="us-gaap",
        tag="Revenues",
        unit="USD",
        period_end=date(2024, 12, 31),
        filed_date=date(2025, 2, 14),
        dimensions={},
    )
    assert xbrl_fact_id(**kwargs) == xbrl_fact_id(**kwargs)


def test_xbrl_fact_id_changes_with_dimensions() -> None:
    from invest_ml.utils import xbrl_fact_id

    base = dict(
        company_id="abc",
        taxonomy="us-gaap",
        tag="Revenue",
        unit="USD",
        period_end=date(2024, 12, 31),
        filed_date=date(2025, 2, 1),
        dimensions={},
    )
    with_dim = {**base, "dimensions": {"segment": "north_america"}}
    assert xbrl_fact_id(**base) != xbrl_fact_id(**with_dim)


def test_assert_no_lookahead_passes_when_available_at_equals_as_of() -> None:
    from invest_ml.utils import assert_no_lookahead

    assert_no_lookahead(date(2024, 12, 31), date(2024, 12, 31))  # should not raise


def test_assert_no_lookahead_passes_when_available_at_before_as_of() -> None:
    from invest_ml.utils import assert_no_lookahead

    assert_no_lookahead(date(2024, 6, 30), date(2024, 12, 31))  # should not raise


def test_assert_no_lookahead_raises_when_available_at_after_as_of() -> None:
    from invest_ml.utils import assert_no_lookahead

    with pytest.raises(ValueError, match="Point-in-time violation"):
        assert_no_lookahead(date(2025, 1, 1), date(2024, 12, 31))


def test_source_lineage_hash_is_stable() -> None:
    from invest_ml.utils import source_lineage_hash

    lineage = {"metric_ids": ["a", "b"], "feature_set_id": "xyz"}
    assert source_lineage_hash(lineage) == source_lineage_hash(lineage)


def test_source_lineage_hash_changes_when_lineage_changes() -> None:
    from invest_ml.utils import source_lineage_hash

    h1 = source_lineage_hash({"metric_ids": ["a"]})
    h2 = source_lineage_hash({"metric_ids": ["b"]})
    assert h1 != h2
