from __future__ import annotations

from aarchtune.benchmark.request_metrics import (
    aggregate_benchmark_statistics,
    aggregate_quality,
)


def test_zero_denominator_aggregates_are_explicitly_unavailable() -> None:
    benchmark = aggregate_benchmark_statistics(
        "run", [], configured_attempts=0, measured_interval_seconds=0
    )
    quality = aggregate_quality("run", [])
    assert benchmark.requests_per_minute.available is False
    assert benchmark.latency_seconds.count == 0
    assert benchmark.prompt_tokens_processed == 0
    assert quality.task_attempt_success_rate is None
    assert quality.request_success_rate is None
    assert quality.validator_pass_rate is None
    assert quality.json_validity_rate is None
    assert "task_attempt_success_rate" in quality.unavailable_reasons
    assert "json_validity_rate" in quality.unavailable_reasons
