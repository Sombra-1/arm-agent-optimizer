"""Request and quality aggregation for sequential measured attempts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from aarchtune.benchmark.models import (
    BaselineQualitySummary,
    BenchmarkStatistics,
    OptionalMetric,
    QualityGroupStatistics,
    TaskAttemptResult,
    ValidatorQualityStatistics,
    unavailable_metric,
)
from aarchtune.benchmark.statistics import numeric_statistics
from aarchtune.workload.schema import ValidatorType


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate_benchmark_statistics(
    run_id: str,
    attempts: Sequence[TaskAttemptResult],
    *,
    configured_attempts: int,
    measured_interval_seconds: float,
) -> BenchmarkStatistics:
    measurements = [attempt.measurement for attempt in attempts]
    successful = [item for item in measurements if item.execution.request_succeeded]
    requests_per_minute = (
        OptionalMetric(
            value=len(successful) * 60.0 / measured_interval_seconds,
            available=True,
            source="client_derived",
            source_path="successful_requests/measured_wall_clock_interval",
        )
        if measured_interval_seconds > 0
        else unavailable_metric("Measured wall-clock interval was not positive")
    )

    def values(name: str) -> list[OptionalMetric]:
        return [getattr(item.tokens, name) for item in measurements]

    prompt_tokens = values("prompt_tokens")
    completion_tokens = values("completion_tokens")
    errors = [item.execution.failure_kind for item in measurements]
    return BenchmarkStatistics(
        run_id=run_id,
        total_configured_attempts=configured_attempts,
        measured_attempts_completed=len(measurements),
        successful_requests=len(successful),
        failed_requests=len(measurements) - len(successful),
        timeout_count=sum(item.execution.timed_out for item in measurements),
        http_failure_count=sum(kind in {"http_error", "server_error"} for kind in errors),
        invalid_response_failure_count=sum(
            kind in {"invalid_json", "missing_completion_content", "response_too_large"}
            for kind in errors
        ),
        measured_interval_seconds=measured_interval_seconds,
        requests_per_minute=requests_per_minute,
        latency_seconds=numeric_statistics(
            item.timing.duration_ns / 1_000_000_000 for item in measurements
        ),
        prompt_tokens=numeric_statistics(prompt_tokens),
        completion_tokens=numeric_statistics(completion_tokens),
        server_prompt_tokens_per_second=numeric_statistics(
            values("server_prompt_tokens_per_second")
        ),
        server_generation_tokens_per_second=numeric_statistics(
            values("server_generation_tokens_per_second")
        ),
        client_prompt_tokens_per_second=numeric_statistics(
            values("client_prompt_tokens_per_second")
        ),
        client_completion_tokens_per_second=numeric_statistics(
            values("client_completion_tokens_per_second")
        ),
        prompt_tokens_processed=sum(
            int(metric.value)
            for metric in prompt_tokens
            if metric.available and metric.value is not None
        ),
        completion_tokens_generated=sum(
            int(metric.value)
            for metric in completion_tokens
            if metric.available and metric.value is not None
        ),
    )


def aggregate_quality(run_id: str, attempts: Sequence[TaskAttemptResult]) -> BaselineQualitySummary:
    task_results = [attempt.raw.validation for attempt in attempts]
    passed = sum(result.passed is True for result in task_results)
    validator_results = [result for task in task_results for result in task.validator_results]
    validator_passed = sum(result.passed for result in validator_results)
    by_task: dict[str, list[bool]] = defaultdict(list)
    by_category: dict[str, list[bool]] = defaultdict(list)
    by_validator: dict[ValidatorType, list[bool]] = defaultdict(list)
    for result in task_results:
        by_task[result.task_id].append(result.passed is True)
        by_category[result.category].append(result.passed is True)
        for validator in result.validator_results:
            by_validator[validator.validator].append(validator.passed)
    json_results = [
        result.passed
        for result in validator_results
        if result.validator is ValidatorType.VALID_JSON
    ]
    responses = [attempt.measurement.execution for attempt in attempts]
    total = len(attempts)
    unavailable: dict[str, str] = {}
    if total == 0:
        unavailable.update(
            {
                "task_attempt_success_rate": "No task attempts completed",
                "request_success_rate": "No task attempts completed",
                "timeout_rate": "No task attempts completed",
            }
        )
    if not validator_results:
        unavailable["validator_pass_rate"] = "No validators were evaluated"
    if not json_results:
        unavailable["json_validity_rate"] = "No valid_json validators were evaluated"
    return BaselineQualitySummary(
        run_id=run_id,
        task_attempts=total,
        passed_task_attempts=passed,
        failed_task_attempts=total - passed,
        task_attempt_success_rate=_rate(passed, total),
        unique_tasks_passing_every_repetition=sorted(
            task_id for task_id, outcomes in by_task.items() if all(outcomes)
        ),
        unique_tasks_failing_at_least_once=sorted(
            task_id for task_id, outcomes in by_task.items() if not all(outcomes)
        ),
        validator_pass_count=validator_passed,
        validator_failure_count=len(validator_results) - validator_passed,
        validator_pass_rate=_rate(validator_passed, len(validator_results)),
        json_valid_response_count=sum(json_results),
        json_validity_rate=_rate(sum(json_results), len(json_results)),
        request_success_count=sum(response.request_succeeded for response in responses),
        request_success_rate=_rate(
            sum(response.request_succeeded for response in responses), total
        ),
        timeout_count=sum(response.timed_out for response in responses),
        timeout_rate=_rate(sum(response.timed_out for response in responses), total),
        per_category={
            category: QualityGroupStatistics(
                attempts=len(outcomes),
                passed=sum(outcomes),
                failed=len(outcomes) - sum(outcomes),
                success_rate=_rate(sum(outcomes), len(outcomes)),
            )
            for category, outcomes in sorted(by_category.items())
        },
        per_validator_type={
            validator: ValidatorQualityStatistics(
                total=len(outcomes),
                passed=sum(outcomes),
                failed=len(outcomes) - sum(outcomes),
                pass_rate=_rate(sum(outcomes), len(outcomes)),
            )
            for validator, outcomes in sorted(by_validator.items(), key=lambda item: item[0].value)
        },
        unavailable_reasons=unavailable,
    )
