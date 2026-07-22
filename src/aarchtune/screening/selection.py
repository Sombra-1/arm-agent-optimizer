"""Transparent low-level scoring and diverse candidate advancement."""

from __future__ import annotations

from collections import defaultdict

from aarchtune.optimization.models import CandidateProfile, OptimizationGoal
from aarchtune.screening.models import (
    CandidateAdvancementDecision,
    DecisionStatus,
    MetricKind,
    ScoreComponent,
    SignatureMembership,
    SignatureScreeningResult,
    StabilityClass,
)

_WEIGHTS: dict[OptimizationGoal, dict[str, float]] = {
    OptimizationGoal.LATENCY: {"decode": 0.55, "combined": 0.30, "stability": 0.15},
    OptimizationGoal.THROUGHPUT: {
        "prefill": 0.30,
        "decode": 0.35,
        "combined": 0.25,
        "stability": 0.10,
    },
    OptimizationGoal.MEMORY: {
        "inverse_peak_rss": 0.60,
        "decode": 0.15,
        "prefill": 0.15,
        "stability": 0.10,
    },
    OptimizationGoal.BALANCED: {
        "prefill": 0.35,
        "decode": 0.35,
        "inverse_peak_rss": 0.15,
        "stability": 0.15,
    },
}


def _raw_components(result: SignatureScreeningResult) -> dict[str, float | None]:
    by_kind: dict[MetricKind, list[float]] = defaultdict(list)
    for aggregate in result.scenario_aggregates:
        if aggregate.throughput.mean is not None:
            by_kind[aggregate.metric_kind].append(aggregate.throughput.mean)
    stability = {
        StabilityClass.STABLE: 1.0,
        StabilityClass.VARIABLE: 0.7,
        StabilityClass.INSUFFICIENT_DATA: 0.4,
        StabilityClass.HIGHLY_VARIABLE: 0.0,
    }[result.stability.classification]
    return {
        "prefill": max(by_kind[MetricKind.PREFILL], default=None),
        "decode": max(by_kind[MetricKind.DECODE], default=None),
        "combined": max(by_kind[MetricKind.COMBINED], default=None),
        "inverse_peak_rss": (
            float(result.process_peak_rss_bytes)
            if result.process_peak_rss_bytes is not None
            else None
        ),
        "stability": stability,
    }


def score_signatures(
    results: list[SignatureScreeningResult], goal: OptimizationGoal
) -> list[SignatureScreeningResult]:
    eligible = [result for result in results if result.screening_eligible]
    raw = {result.signature_id: _raw_components(result) for result in eligible}
    normalized: dict[tuple[str, str], float] = {}
    for component in _WEIGHTS[goal]:
        available = [values[component] for values in raw.values() if values[component] is not None]
        numeric = [float(value) for value in available if value is not None]
        for signature_id, values in raw.items():
            value = values[component]
            if value is None:
                continue
            if component == "inverse_peak_rss":
                low, high = min(numeric), max(numeric)
                score = 1.0 if low == high else (high - value) / (high - low)
            else:
                low, high = min(numeric), max(numeric)
                score = 1.0 if low == high else (value - low) / (high - low)
            normalized[(signature_id, component)] = score
    updated: list[SignatureScreeningResult] = []
    for result in results:
        if not result.screening_eligible:
            updated.append(result)
            continue
        raw_values = raw[result.signature_id]
        available_weights = {
            name: weight for name, weight in _WEIGHTS[goal].items() if raw_values[name] is not None
        }
        total_weight = sum(available_weights.values())
        components: list[ScoreComponent] = []
        score = 0.0
        for name, configured_weight in _WEIGHTS[goal].items():
            value = raw_values[name]
            if value is None or total_weight == 0:
                components.append(
                    ScoreComponent(
                        component=name,
                        raw_value=None,
                        normalized_value=None,
                        weight=0.0,
                        contribution=None,
                        available=False,
                        reason="Component was unavailable and weights were renormalized",
                    )
                )
                continue
            weight = configured_weight / total_weight
            normalized_value = normalized[(result.signature_id, name)]
            contribution = normalized_value * weight
            score += contribution
            components.append(
                ScoreComponent(
                    component=name,
                    raw_value=value,
                    normalized_value=normalized_value,
                    weight=weight,
                    contribution=contribution,
                    available=True,
                )
            )
        bounded_score = min(1.0, max(0.0, score))
        updated.append(
            result.model_copy(update={"score": bounded_score, "score_components": components})
        )
    return updated


def select_candidates(
    candidates: list[CandidateProfile],
    memberships: list[SignatureMembership],
    results: list[SignatureScreeningResult],
    advance_count: int,
) -> tuple[list[CandidateProfile], list[CandidateAdvancementDecision]]:
    membership_by_candidate = {item.candidate_id: item for item in memberships}
    result_by_signature = {item.signature_id: item for item in results}
    eligible = []
    for candidate in candidates:
        membership = membership_by_candidate[candidate.id]
        result = result_by_signature.get(membership.bench_signature_id)
        if result is not None and result.screening_eligible and candidate.executable:
            eligible.append(candidate)
    selected: list[CandidateProfile] = []
    baseline = next((candidate for candidate in eligible if candidate.baseline), None)
    if baseline is not None:
        selected.append(baseline)
    covered: dict[str, set[object]] = defaultdict(set)

    def cover(candidate: CandidateProfile) -> None:
        for field in (
            "threads",
            "batch_size",
            "ubatch_size",
            "parallel_slots",
            "prompt_cache",
            "backend_label",
        ):
            covered[field].add(getattr(candidate.runtime, field))

    for candidate in selected:
        cover(candidate)
    remaining = [candidate for candidate in eligible if candidate not in selected]
    if baseline is not None:
        for field in ("prompt_cache", "parallel_slots"):
            if len(selected) >= advance_count:
                break
            baseline_value = getattr(baseline.runtime, field)
            alternatives = [
                candidate
                for candidate in remaining
                if getattr(candidate.runtime, field) != baseline_value
            ]
            if alternatives:
                chosen = min(
                    alternatives,
                    key=lambda candidate: (
                        -(
                            result_by_signature[
                                membership_by_candidate[candidate.id].bench_signature_id
                            ].score
                            or 0.0
                        ),
                        candidate.id,
                    ),
                )
                selected.append(chosen)
                cover(chosen)
                remaining.remove(chosen)
    while remaining and len(selected) < advance_count:

        def key(candidate: CandidateProfile) -> tuple[float, int, str]:
            membership = membership_by_candidate[candidate.id]
            result = result_by_signature[membership.bench_signature_id]
            gain = sum(
                getattr(candidate.runtime, field) not in covered[field]
                for field in (
                    "threads",
                    "batch_size",
                    "ubatch_size",
                    "parallel_slots",
                    "prompt_cache",
                    "backend_label",
                )
            )
            diversity_adjusted = (result.score or 0.0) + 0.05 * gain
            return (-diversity_adjusted, -gain, candidate.id)

        chosen = min(remaining, key=key)
        selected.append(chosen)
        cover(chosen)
        remaining.remove(chosen)
    selected_ids = {candidate.id for candidate in selected}
    decisions = []
    signature_selected_counts: dict[str, int] = defaultdict(int)
    for candidate in selected:
        signature_selected_counts[membership_by_candidate[candidate.id].bench_signature_id] += 1
    for candidate in candidates:
        membership = membership_by_candidate[candidate.id]
        result = result_by_signature.get(membership.bench_signature_id)
        if not candidate.executable:
            decision, code, reason = (
                DecisionStatus.EXCLUDED,
                "unsupported_bench_mapping",
                "Candidate is not executable according to the validated search plan",
            )
        elif result is None:
            decision, code, reason = (
                DecisionStatus.UNSCREENABLE,
                "unsupported_bench_mapping",
                "No executable low-level signature represented this candidate",
            )
        elif not result.screening_eligible:
            decision, code, reason = (
                DecisionStatus.SCREENING_FAILED,
                result.reasons[0] if result.reasons else "required_scenario_failed",
                "The low-level signature did not satisfy screening eligibility",
            )
        elif candidate.id in selected_ids:
            if candidate.baseline:
                code = "baseline_retained"
                reason = "Baseline candidate retained after successful low-level screening"
            elif signature_selected_counts[membership.bench_signature_id] > 1:
                differing_cache = candidate.runtime.prompt_cache
                code = "diversity_prompt_cache" if differing_cache else "diversity_parallel_slots"
                reason = (
                    "Advanced as a server-only variant of a strong low-level signature; "
                    "the unscreenable behavior remains for later workload evaluation"
                )
            else:
                code = "high_screening_score"
                reason = "Advanced using low-level score with deterministic configuration diversity"
            decision = DecisionStatus.ADVANCED
        else:
            decision = DecisionStatus.NOT_ADVANCED
            code = (
                "duplicate_server_profile"
                if signature_selected_counts[membership.bench_signature_id]
                else "low_screening_score"
            )
            reason = "Candidate was not selected within the bounded diverse advancement set"
        decisions.append(
            CandidateAdvancementDecision(
                candidate_id=candidate.id,
                candidate_hash=candidate.profile_hash,
                signature_id=membership.bench_signature_id,
                decision=decision,
                reason_code=code,
                reason=reason,
                screening_score=result.score if result is not None else None,
            )
        )
    return selected, decisions
