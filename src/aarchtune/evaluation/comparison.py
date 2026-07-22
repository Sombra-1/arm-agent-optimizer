"""Performance comparability and baseline-relative improvement calculations."""

from __future__ import annotations

from aarchtune.evaluation.models import (
    CandidateComparison,
    CandidateExecutionResult,
    MetricImprovement,
)


def relative_improvement(
    baseline: float | None, candidate: float | None, *, higher_is_better: bool
) -> tuple[float | None, str | None]:
    if baseline is None or candidate is None:
        return None, "Baseline or candidate metric is unavailable"
    if baseline <= 0 or candidate < 0:
        return None, "Metric values must be non-negative and baseline must be positive"
    value = (
        (candidate - baseline) / baseline if higher_is_better else (baseline - candidate) / baseline
    )
    return value, None


def compare_candidate(
    candidate: CandidateExecutionResult, baseline: CandidateExecutionResult
) -> CandidateComparison:
    if candidate.performance is None or baseline.performance is None:
        return CandidateComparison(
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            comparable=False,
            reasons=["Candidate or fresh baseline performance is unavailable"],
            improvements=[],
        )
    pairs = (
        (
            "requests_per_minute",
            baseline.performance.requests_per_minute,
            candidate.performance.requests_per_minute,
            True,
        ),
        (
            "median_latency_seconds",
            baseline.performance.median_latency_seconds,
            candidate.performance.median_latency_seconds,
            False,
        ),
        (
            "p95_latency_seconds",
            baseline.performance.p95_latency_seconds,
            candidate.performance.p95_latency_seconds,
            False,
        ),
        (
            "measured_peak_rss_bytes",
            float(baseline.performance.measured_peak_rss_bytes)
            if baseline.performance.measured_peak_rss_bytes is not None
            else None,
            float(candidate.performance.measured_peak_rss_bytes)
            if candidate.performance.measured_peak_rss_bytes is not None
            else None,
            False,
        ),
        (
            "server_generation_throughput",
            baseline.performance.server_generation_throughput,
            candidate.performance.server_generation_throughput,
            True,
        ),
    )
    improvements = []
    for metric, baseline_value, candidate_value, higher in pairs:
        value, reason = relative_improvement(
            baseline_value, candidate_value, higher_is_better=higher
        )
        improvements.append(
            MetricImprovement(
                metric=metric,
                baseline_value=baseline_value,
                candidate_value=candidate_value,
                improvement=value,
                available=value is not None,
                higher_is_better=higher,
                reason=reason,
            )
        )
    core = {"requests_per_minute", "p95_latency_seconds", "measured_peak_rss_bytes"}
    missing = [item.metric for item in improvements if item.metric in core and not item.available]
    return CandidateComparison(
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.candidate_hash,
        comparable=not missing and candidate.performance.comparable,
        reasons=[f"Required comparison metric unavailable: {name}" for name in missing],
        improvements=improvements,
    )


def improvement_value(comparison: CandidateComparison, metric: str) -> float | None:
    return next(
        (item.improvement for item in comparison.improvements if item.metric == metric), None
    )
