"""Build the exact structured input consumed by the static report renderer."""

from __future__ import annotations

import shlex
from datetime import UTC, datetime
from typing import cast

from pydantic import JsonValue

from aarchtune.evaluation.models import QualityGateStatus
from aarchtune.finalization.context import FinalizationContext
from aarchtune.finalization.models import OptimizationPassport, ParetoFrontier, ReportData


def _improvements(context: FinalizationContext) -> dict[str, JsonValue]:
    selected = context.selection.selected_candidate_id
    comparison = next((item for item in context.comparisons if item.candidate_id == selected), None)
    return cast(
        dict[str, JsonValue],
        {
            item.metric: item.improvement if item.available else None
            for item in comparison.improvements
        }
        if comparison
        else {},
    )


def create_report_data(
    context: FinalizationContext,
    passport: OptimizationPassport,
    pareto: ParetoFrontier,
) -> ReportData:
    decisions = {item.candidate_id: item for item in context.decisions}
    candidates: list[dict[str, JsonValue]] = []
    for result in context.results:
        performance = result.performance
        quality = result.quality
        decision = decisions[result.candidate_id]
        candidates.append(
            cast(
                dict[str, JsonValue],
                {
                    "id": result.candidate_id,
                    "hash": result.candidate_hash,
                    "baseline": result.profile.baseline,
                    "selected": result.candidate_id == context.selection.selected_candidate_id,
                    "execution_status": result.status.value,
                    "quality_status": decision.status.value,
                    "requests_per_minute": performance.requests_per_minute if performance else None,
                    "median_latency_seconds": (
                        performance.median_latency_seconds if performance else None
                    ),
                    "p95_latency_seconds": performance.p95_latency_seconds if performance else None,
                    "peak_rss_bytes": performance.measured_peak_rss_bytes if performance else None,
                    "task_success_rate": (
                        quality.aggregate.task_attempt_success_rate if quality else None
                    ),
                    "json_validity_rate": quality.aggregate.json_validity_rate if quality else None,
                    "validator_pass_rate": quality.aggregate.validator_pass_rate
                    if quality
                    else None,
                    "violations": [item.model_dump(mode="json") for item in decision.violations],
                },
            )
        )
    selected_candidate = next(
        (item for item in candidates if item.get("id") == context.selection.selected_candidate_id),
        None,
    )
    fastest = passport.fastest_rejected_candidate
    if fastest and selected_candidate:
        selected_rpm = selected_candidate.get("requests_per_minute")
        rejected_rpm = fastest.get("requests_per_minute")
        if (
            not isinstance(selected_rpm, (int, float))
            or not isinstance(rejected_rpm, (int, float))
            or rejected_rpm <= selected_rpm
        ):
            fastest = None
    hardware = context.manifest.hardware_fingerprint
    runtime = context.manifest.runtime_fingerprint
    model = context.manifest.model_fingerprint
    workload = context.manifest.workload_fingerprint
    assert (
        hardware is not None and runtime is not None and model is not None and workload is not None
    )
    command = " ".join(shlex.quote(item) for item in (passport.selected_command or []))
    selected_result = next(
        (
            item
            for item in context.results
            if item.candidate_id == context.selection.selected_candidate_id
        ),
        None,
    )
    selected_quality = (
        selected_result.quality.aggregate if selected_result and selected_result.quality else None
    )
    return ReportData(
        generated_at=datetime.now(UTC),
        evaluation_id=context.summary.evaluation_id,
        passport_id=passport.passport_id,
        synthetic=context.summary.synthetic_fixture,
        outcome=context.selection.outcome.value,
        selected_candidate_id=context.selection.selected_candidate_id,
        baseline_candidate_id=context.selection.baseline_candidate_id,
        hero=cast(
            dict[str, JsonValue],
            {
                "selection_reason": context.selection.reason,
                "improvements": _improvements(context),
                "quality_preserved": (
                    context.selection.selected_candidate_id is not None
                    and decisions[context.selection.selected_candidate_id].status
                    is QualityGateStatus.PASSED
                ),
            },
        ),
        funnel={
            "planned": len(context.search_plan.candidates),
            "low_level_signatures": context.screening_summary.bench_signatures,
            "screened": context.screening_summary.successful_signatures
            + context.screening_summary.partial_signatures,
            "advanced": context.screening_summary.advanced_candidates,
            "real_workload_evaluated": len(context.results),
            "quality_passed": context.summary.quality_passed,
        },
        candidates=candidates,
        pareto=pareto,
        quality_policy=cast(dict[str, JsonValue], context.policy.policy.model_dump(mode="json")),
        drift=cast(dict[str, JsonValue], context.drift.model_dump(mode="json")),
        hardware=cast(dict[str, JsonValue], hardware.model_dump(mode="json")),
        runtime=cast(dict[str, JsonValue], runtime.model_dump(mode="json")),
        model=cast(dict[str, JsonValue], model.model_dump(mode="json")),
        workload=cast(dict[str, JsonValue], workload.model_dump(mode="json")),
        selected_settings=(
            context.selected_profile.runtime_configuration if context.selected_profile else None
        ),
        fastest_rejected=fastest,
        per_category_quality=cast(
            dict[str, JsonValue],
            {
                key: value.model_dump(mode="json")
                for key, value in (
                    selected_quality.per_category.items() if selected_quality else []
                )
            },
        ),
        per_validator_quality=cast(
            dict[str, JsonValue],
            {
                key: value.model_dump(mode="json")
                for key, value in (
                    selected_quality.per_validator_type.items() if selected_quality else []
                )
            },
        ),
        reproduction_command=command,
        artifact_hashes=passport.stage_artifact_hashes,
        limitations=passport.limitations,
    )
