"""Fresh baseline start/end environmental drift sentinel."""

from __future__ import annotations

from aarchtune.evaluation.models import (
    CandidateExecutionResult,
    DriftAssessment,
    DriftClassification,
    DriftMetric,
    QualityPolicy,
)


def _relative(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start <= 0:
        return None
    return abs(end - start) / start


def _absolute(start: float | None, end: float | None) -> float | None:
    return abs(end - start) if start is not None and end is not None else None


def assess_drift(
    start: CandidateExecutionResult,
    end: CandidateExecutionResult,
    policy: QualityPolicy,
) -> DriftAssessment:
    if start.performance is None or start.quality is None:
        return DriftAssessment(
            classification=DriftClassification.INVALIDATING,
            metrics=[],
            reasons=["Fresh baseline-start execution was unavailable"],
        )
    if end.performance is None or end.quality is None:
        return DriftAssessment(
            classification=DriftClassification.INVALIDATING,
            metrics=[],
            reasons=["Baseline-end sentinel failed or produced insufficient evidence"],
        )
    performance_pairs = (
        (
            "median_latency_seconds",
            start.performance.median_latency_seconds,
            end.performance.median_latency_seconds,
            policy.drift.performance.median_latency_relative_change,
        ),
        (
            "p95_latency_seconds",
            start.performance.p95_latency_seconds,
            end.performance.p95_latency_seconds,
            policy.drift.performance.p95_latency_relative_change,
        ),
        (
            "requests_per_minute",
            start.performance.requests_per_minute,
            end.performance.requests_per_minute,
            policy.drift.performance.requests_per_minute_relative_change,
        ),
        (
            "server_generation_throughput",
            start.performance.server_generation_throughput,
            end.performance.server_generation_throughput,
            policy.drift.performance.generation_throughput_relative_change,
        ),
    )
    quality_pairs = (
        (
            "task_success_rate",
            start.quality.aggregate.task_attempt_success_rate,
            end.quality.aggregate.task_attempt_success_rate,
            policy.drift.quality.task_success_absolute_change,
        ),
        (
            "json_validity_rate",
            start.quality.aggregate.json_validity_rate,
            end.quality.aggregate.json_validity_rate,
            policy.drift.quality.json_validity_absolute_change,
        ),
        (
            "validator_pass_rate",
            start.quality.aggregate.validator_pass_rate,
            end.quality.aggregate.validator_pass_rate,
            policy.drift.quality.validator_pass_absolute_change,
        ),
    )
    metrics: list[DriftMetric] = []
    for name, start_value, end_value, threshold in performance_pairs:
        change = _relative(start_value, end_value)
        classification = (
            DriftClassification.UNAVAILABLE
            if change is None
            else DriftClassification.WARNING
            if change > threshold
            else DriftClassification.STABLE
        )
        metrics.append(
            DriftMetric(
                metric=name,
                start_value=start_value,
                end_value=end_value,
                observed_change=change,
                threshold=threshold,
                absolute_change=False,
                classification=classification,
                reason=(
                    "Metric unavailable"
                    if change is None
                    else "Performance movement exceeds drift warning threshold"
                    if change > threshold
                    else "Performance movement is within threshold"
                ),
            )
        )
    for name, start_value, end_value, threshold in quality_pairs:
        change = _absolute(start_value, end_value)
        classification = (
            DriftClassification.UNAVAILABLE
            if change is None
            else DriftClassification.INVALIDATING
            if change > threshold
            else DriftClassification.STABLE
        )
        metrics.append(
            DriftMetric(
                metric=name,
                start_value=start_value,
                end_value=end_value,
                observed_change=change,
                threshold=threshold,
                absolute_change=True,
                classification=classification,
                reason=(
                    "Metric unavailable"
                    if change is None
                    else "Quality movement exceeds invalidating drift threshold"
                    if change > threshold
                    else "Quality movement is within threshold"
                ),
            )
        )
    if any(item.classification is DriftClassification.INVALIDATING for item in metrics):
        classification = DriftClassification.INVALIDATING
    elif any(item.classification is DriftClassification.WARNING for item in metrics):
        classification = DriftClassification.WARNING
    elif all(item.classification is DriftClassification.UNAVAILABLE for item in metrics):
        classification = DriftClassification.UNAVAILABLE
    else:
        classification = DriftClassification.STABLE
    return DriftAssessment(
        classification=classification,
        metrics=metrics,
        reasons=[
            item.reason for item in metrics if item.classification is not DriftClassification.STABLE
        ],
    )
