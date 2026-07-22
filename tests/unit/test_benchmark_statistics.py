from __future__ import annotations

import math

import pytest

from aarchtune.benchmark.models import OptionalMetric
from aarchtune.benchmark.statistics import numeric_statistics


def test_empty_statistics_are_unavailable() -> None:
    result = numeric_statistics([])
    assert result.count == 0
    assert result.p95 is None
    assert result.unavailable_reason


def test_one_value_has_same_min_median_and_p95() -> None:
    result = numeric_statistics([123])
    assert (result.minimum, result.median, result.maximum, result.p95) == (
        123.0,
        123.0,
        123.0,
        123.0,
    )
    assert result.standard_deviation is None


@pytest.mark.parametrize(
    ("values", "median"),
    [([1, 2, 3], 2.0), ([1, 2, 3, 4], 2.5)],
)
def test_even_and_odd_median(values: list[int], median: float) -> None:
    result = numeric_statistics(values)
    assert result.median == median
    assert result.mean == sum(values) / len(values)
    assert result.standard_deviation is not None


def test_p95_uses_nearest_rank() -> None:
    result = numeric_statistics(range(1, 21))
    assert result.p95 == 19


def test_unavailable_and_nonfinite_values_are_ignored() -> None:
    unavailable = OptionalMetric(value=None, available=False, reason="missing")
    result = numeric_statistics([unavailable, math.nan, math.inf, 7])
    assert result.count == 1
    assert result.mean == 7


def test_nanosecond_scale_precision_is_preserved() -> None:
    result = numeric_statistics([0.000000001, 0.000000002])
    assert result.minimum == 1e-9
    assert result.maximum == 2e-9
