"""Presentation-oriented Pareto frontier over real-workload measurements."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from aarchtune.evaluation.models import (
    CandidateExecutionResult,
    QualityDecision,
    QualityGateStatus,
    SelectionDecision,
)
from aarchtune.finalization.models import ParetoFrontier, ParetoRecord


def _dominates(left: ParetoRecord, right: ParetoRecord) -> bool:
    no_worse = (
        left.requests_per_minute >= right.requests_per_minute
        and left.p95_latency_seconds <= right.p95_latency_seconds
        and left.peak_rss_bytes <= right.peak_rss_bytes
        and left.task_success_rate >= right.task_success_rate
    )
    better = (
        left.requests_per_minute > right.requests_per_minute
        or left.p95_latency_seconds < right.p95_latency_seconds
        or left.peak_rss_bytes < right.peak_rss_bytes
        or left.task_success_rate > right.task_success_rate
    )
    return no_worse and better


def calculate_pareto_frontier(
    evaluation_id: str,
    results: list[CandidateExecutionResult],
    decisions: list[QualityDecision],
    selection: SelectionDecision,
) -> ParetoFrontier:
    decision_by_id = {item.candidate_id: item for item in decisions}
    provisional: list[ParetoRecord] = []
    excluded: list[dict[str, JsonValue]] = []
    for result in results:
        decision = decision_by_id.get(result.candidate_id)
        performance = result.performance
        quality = result.quality
        missing = []
        if performance is None or quality is None:
            missing.append("execution evidence")
        elif (
            performance.requests_per_minute is None
            or performance.p95_latency_seconds is None
            or performance.measured_peak_rss_bytes is None
            or quality.aggregate.task_attempt_success_rate is None
        ):
            missing.append("one or more core Pareto metrics")
        if decision is None or decision.status is not QualityGateStatus.PASSED:
            missing.append("quality gate did not pass")
        if missing or performance is None or quality is None:
            excluded.append(
                cast(
                    dict[str, JsonValue],
                    {"candidate_id": result.candidate_id, "reasons": missing or ["unavailable"]},
                )
            )
            continue
        assert decision is not None
        task_success = quality.aggregate.task_attempt_success_rate
        assert performance.requests_per_minute is not None
        assert performance.p95_latency_seconds is not None
        assert performance.measured_peak_rss_bytes is not None
        assert task_success is not None
        provisional.append(
            ParetoRecord(
                candidate_id=result.candidate_id,
                candidate_hash=result.candidate_hash,
                baseline=result.profile.baseline,
                selected=result.candidate_id == selection.selected_candidate_id,
                quality_status=decision.status.value,
                requests_per_minute=performance.requests_per_minute,
                p95_latency_seconds=performance.p95_latency_seconds,
                peak_rss_bytes=performance.measured_peak_rss_bytes,
                task_success_rate=task_success,
                dominated=False,
                dominating_candidate_ids=[],
            )
        )
    records = []
    for record in provisional:
        dominators = sorted(
            other.candidate_id
            for other in provisional
            if other.candidate_id != record.candidate_id and _dominates(other, record)
        )
        records.append(
            record.model_copy(
                update={"dominated": bool(dominators), "dominating_candidate_ids": dominators}
            )
        )
    return ParetoFrontier(
        evaluation_id=evaluation_id,
        records=sorted(records, key=lambda item: item.candidate_id),
        excluded=sorted(excluded, key=lambda item: str(item["candidate_id"])),
    )
