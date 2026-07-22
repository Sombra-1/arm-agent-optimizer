"""Conservative memory-risk guardrails without exact memory prediction."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from aarchtune.optimization.models import (
    CandidateResourceEstimate,
    MemoryRiskClass,
    ProfileRuntime,
    SearchPlanInput,
)


def assess_memory_risk(
    runtime: ProfileRuntime, plan_input: SearchPlanInput
) -> CandidateResourceEstimate:
    baseline = plan_input.baseline_runtime
    peak = plan_input.baseline_peak_rss_bytes
    available = plan_input.hardware.available_memory_bytes
    model_size = plan_input.model.size_bytes
    baseline_slots = baseline.parallel_slots or 1
    candidate_slots = runtime.parallel_slots or 1
    context_increase = (
        baseline.context_size is not None
        and runtime.context_size is not None
        and runtime.context_size > baseline.context_size
    )
    inputs: dict[str, JsonValue] = {
        "model_file_size_bytes": model_size,
        "available_memory_bytes": available,
        "total_memory_bytes": plan_input.hardware.total_memory_bytes,
        "baseline_peak_rss_bytes": peak,
        "baseline_parallel_slots": baseline_slots,
        "candidate_parallel_slots": candidate_slots,
        "baseline_context_size": baseline.context_size,
        "candidate_context_size": runtime.context_size,
    }
    assumptions = [
        "Model file size is not treated as exact resident memory",
        "Parallelism and context changes are risk signals, not exact scaling formulas",
    ]
    if available is not None and model_size > available:
        return CandidateResourceEstimate(
            classification=MemoryRiskClass.HIGH_RISK,
            estimated_memory_bytes=None,
            available=False,
            method="capacity-guardrail",
            inputs=inputs,
            assumptions=assumptions,
            confidence="low",
            reason=(
                "Model file alone exceeds currently available memory; execution has "
                "dangerously low headroom"
            ),
        )
    if peak is None or available is None:
        reason = (
            "Baseline peak RSS is unavailable"
            if peak is None
            else "Available memory is unavailable"
        )
        if candidate_slots > baseline_slots or context_increase:
            reason += "; increased parallelism or context raises unquantified memory risk"
        return CandidateResourceEstimate(
            classification=MemoryRiskClass.UNKNOWN,
            estimated_memory_bytes=None,
            available=False,
            method="insufficient-evidence",
            inputs=inputs,
            assumptions=assumptions,
            confidence="none",
            reason=reason,
        )
    relative_pressure = peak * (candidate_slots / baseline_slots)
    if context_increase:
        relative_pressure *= cast(int, runtime.context_size) / cast(int, baseline.context_size)
    if relative_pressure >= available * 0.9:
        classification = MemoryRiskClass.HIGH_RISK
        reason = (
            "Baseline-relative pressure approaches currently available memory after "
            "parallelism or context increases"
        )
    elif relative_pressure >= available * 0.65 or (
        candidate_slots >= 4 and available - peak < peak * 2
    ):
        classification = MemoryRiskClass.WARNING
        reason = "Baseline-relative memory headroom may be limited for this parallelism"
    else:
        classification = MemoryRiskClass.SAFE
        reason = "Baseline RSS evidence leaves conservative current-memory headroom"
    return CandidateResourceEstimate(
        classification=classification,
        estimated_memory_bytes=None,
        available=False,
        method="baseline-relative-guardrail",
        inputs=inputs,
        assumptions=assumptions,
        confidence="low",
        reason=reason,
    )
