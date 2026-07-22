"""Goal-specific ranking using only quality-passing real-workload results."""

from __future__ import annotations

from aarchtune.evaluation.models import (
    CandidateComparison,
    CandidateExecutionResult,
    CandidateRankingResult,
    QualityDecision,
    QualityGateStatus,
    QualityPolicy,
    RankingComponent,
)
from aarchtune.optimization.models import OptimizationGoal


def _normalize(values: dict[str, float], *, inverse: bool = False) -> dict[str, float]:
    low, high = min(values.values()), max(values.values())
    if low == high:
        return {key: 1.0 for key in values}
    if inverse:
        return {key: (high - value) / (high - low) for key, value in values.items()}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def _eligible(
    results: list[CandidateExecutionResult],
    decisions: list[QualityDecision],
    comparisons: list[CandidateComparison],
) -> list[CandidateExecutionResult]:
    decision_by_id = {item.candidate_id: item for item in decisions}
    comparison_by_id = {item.candidate_id: item for item in comparisons}
    return [
        result
        for result in results
        if result.performance is not None
        and result.quality is not None
        and result.status.value == "completed"
        and decision_by_id[result.candidate_id].status is QualityGateStatus.PASSED
        and comparison_by_id[result.candidate_id].comparable
    ]


def _component(
    name: str, raw: float | None, normalized: float | None, weight: float
) -> RankingComponent:
    return RankingComponent(
        component=name,
        raw_value=raw,
        normalized_value=normalized,
        weight=weight if raw is not None else 0.0,
        contribution=normalized * weight if normalized is not None else None,
        available=raw is not None,
        reason=None if raw is not None else "Metric unavailable",
    )


def rank_candidates(
    results: list[CandidateExecutionResult],
    decisions: list[QualityDecision],
    comparisons: list[CandidateComparison],
    goal: OptimizationGoal,
    policy: QualityPolicy,
) -> list[CandidateRankingResult]:
    eligible = _eligible(results, decisions, comparisons)
    if not eligible:
        return []
    performance = {item.candidate_id: item.performance for item in eligible}
    quality = {item.candidate_id: item.quality for item in eligible}
    if goal is OptimizationGoal.BALANCED:
        raw: dict[str, dict[str, float]] = {}
        for item in eligible:
            metrics = performance[item.candidate_id]
            observed_quality = quality[item.candidate_id]
            if (
                metrics is None
                or observed_quality is None
                or metrics.requests_per_minute is None
                or metrics.p95_latency_seconds is None
                or metrics.measured_peak_rss_bytes is None
            ):
                continue
            rates = observed_quality.aggregate
            margins = [
                (rates.request_success_rate or 0.0) - policy.absolute_minimums.request_success_rate,
                (rates.task_attempt_success_rate or 0.0)
                - policy.absolute_minimums.task_success_rate,
                (rates.json_validity_rate or 0.0) - policy.absolute_minimums.json_validity_rate,
                (rates.validator_pass_rate or 0.0) - policy.absolute_minimums.validator_pass_rate,
            ]
            raw[item.candidate_id] = {
                "requests_per_minute": metrics.requests_per_minute,
                "inverse_p95_latency": metrics.p95_latency_seconds,
                "inverse_peak_rss": float(metrics.measured_peak_rss_bytes),
                "consistency": observed_quality.consistency.per_task_pass_consistency_rate or 0.0,
                "quality_margin": sum(margins) / len(margins),
            }
        if not raw:
            return []
        normalized = {
            "requests_per_minute": _normalize(
                {key: value["requests_per_minute"] for key, value in raw.items()}
            ),
            "inverse_p95_latency": _normalize(
                {key: value["inverse_p95_latency"] for key, value in raw.items()}, inverse=True
            ),
            "inverse_peak_rss": _normalize(
                {key: value["inverse_peak_rss"] for key, value in raw.items()}, inverse=True
            ),
            "consistency": _normalize({key: value["consistency"] for key, value in raw.items()}),
            "quality_margin": _normalize(
                {key: value["quality_margin"] for key, value in raw.items()}
            ),
        }
        configured = policy.balanced_weights.model_dump(exclude={"schema_version"})
        scored: list[tuple[str, float, list[RankingComponent]]] = []
        for candidate_id, values in raw.items():
            components = [
                _component(name, value, normalized[name][candidate_id], float(configured[name]))
                for name, value in values.items()
            ]
            score = sum(item.contribution or 0.0 for item in components)
            scored.append((candidate_id, score, components))
        scored.sort(key=lambda item: (-item[1], item[0]))
        hashes = {item.candidate_id: item.candidate_hash for item in eligible}
        return [
            CandidateRankingResult(
                candidate_id=candidate_id,
                candidate_hash=hashes[candidate_id],
                position=position,
                score=score,
                components=components,
                tie_break_values=[candidate_id],
            )
            for position, (candidate_id, score, components) in enumerate(scored, 1)
        ]

    def key(item: CandidateExecutionResult) -> tuple[object, ...]:
        metrics = item.performance
        observed_quality = item.quality
        assert metrics is not None and observed_quality is not None
        consistency = observed_quality.consistency.per_task_pass_consistency_rate or 0.0
        rss = metrics.measured_peak_rss_bytes or 2**63
        p95 = metrics.p95_latency_seconds or float("inf")
        median = metrics.median_latency_seconds or float("inf")
        rpm = metrics.requests_per_minute or 0.0
        generation = metrics.server_generation_throughput or 0.0
        request_success = metrics.request_success_rate or 0.0
        mean_rss = metrics.mean_measured_rss_bytes or float("inf")
        if goal is OptimizationGoal.LATENCY:
            return (p95, median, -request_success, rss, -consistency, item.candidate_id)
        if goal is OptimizationGoal.THROUGHPUT:
            return (-rpm, -generation, p95, rss, -consistency, item.candidate_id)
        return (rss, mean_rss, p95, -rpm, -consistency, item.candidate_id)

    ordered = sorted(eligible, key=key)
    primary_name = {
        OptimizationGoal.LATENCY: "p95_latency_seconds",
        OptimizationGoal.THROUGHPUT: "requests_per_minute",
        OptimizationGoal.MEMORY: "measured_peak_rss_bytes",
    }[goal]
    rankings = []
    for position, item in enumerate(ordered, 1):
        metrics = item.performance
        assert metrics is not None
        raw_value = getattr(metrics, primary_name)
        rankings.append(
            CandidateRankingResult(
                candidate_id=item.candidate_id,
                candidate_hash=item.candidate_hash,
                position=position,
                score=None,
                components=[_component(primary_name, float(raw_value), None, 1.0)],
                tie_break_values=[item.candidate_id],
            )
        )
    return rankings
