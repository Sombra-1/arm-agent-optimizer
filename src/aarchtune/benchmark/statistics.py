"""Deterministic dependency-free statistical summaries."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable

from aarchtune.benchmark.models import NumericStatistics, OptionalMetric


def numeric_statistics(values: Iterable[int | float | OptionalMetric]) -> NumericStatistics:
    """Summarize finite available values; P95 uses ceil(0.95*n), nearest rank."""

    numeric: list[float] = []
    for value in values:
        candidate = value.value if isinstance(value, OptionalMetric) and value.available else value
        if isinstance(value, OptionalMetric) and not value.available:
            continue
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            continue
        converted = float(candidate)
        if not math.isfinite(converted):
            continue
        numeric.append(converted)
    if not numeric:
        return NumericStatistics(
            count=0,
            minimum=None,
            maximum=None,
            mean=None,
            median=None,
            p95=None,
            standard_deviation=None,
            unavailable_reason="No finite measurements were available",
        )
    ordered = sorted(numeric)
    rank = math.ceil(0.95 * len(ordered))
    return NumericStatistics(
        count=len(ordered),
        minimum=ordered[0],
        maximum=ordered[-1],
        mean=statistics.fmean(ordered),
        median=statistics.median(ordered),
        p95=ordered[rank - 1],
        standard_deviation=statistics.stdev(ordered) if len(ordered) >= 2 else None,
    )
