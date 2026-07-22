"""Practical-improvement guardrail and final machine-specific selection."""

from __future__ import annotations

import uuid

from aarchtune.evaluation.comparison import improvement_value
from aarchtune.evaluation.models import (
    CandidateComparison,
    CandidateRankingResult,
    DriftAssessment,
    DriftClassification,
    QualityPolicy,
    ScreeningEvaluationReference,
    SelectionDecision,
    SelectionOutcome,
)
from aarchtune.optimization.models import CandidateProfile, OptimizationGoal


def select_profile(
    *,
    rankings: list[CandidateRankingResult],
    comparisons: list[CandidateComparison],
    candidates: list[CandidateProfile],
    baseline: CandidateProfile,
    goal: OptimizationGoal,
    policy: QualityPolicy,
    drift: DriftAssessment,
    screening_reference: ScreeningEvaluationReference,
) -> SelectionDecision:
    selection_id = f"selection-{uuid.uuid4().hex[:12]}"
    if drift.classification is DriftClassification.INVALIDATING:
        return SelectionDecision(
            selection_id=selection_id,
            outcome=SelectionOutcome.INVALIDATED_BY_DRIFT,
            selected_candidate_id=None,
            selected_candidate_hash=None,
            baseline_candidate_id=baseline.id,
            goal=goal,
            ranking_position=None,
            applicable_improvement=None,
            practical_improvement_threshold=None,
            reason_code="environment_drift",
            reason="Baseline-end drift invalidated definitive selection",
            screening_reference=screening_reference,
        )
    if not rankings:
        return SelectionDecision(
            selection_id=selection_id,
            outcome=SelectionOutcome.NO_ELIGIBLE_CANDIDATE,
            selected_candidate_id=None,
            selected_candidate_hash=None,
            baseline_candidate_id=baseline.id,
            goal=goal,
            ranking_position=None,
            applicable_improvement=None,
            practical_improvement_threshold=None,
            reason_code="no_eligible_candidate",
            reason="No completed, comparable candidate passed the quality gate",
            screening_reference=screening_reference,
        )
    candidate_by_id = {item.id: item for item in candidates}
    comparison_by_id = {item.candidate_id: item for item in comparisons}
    ranking_by_id = {item.candidate_id: item for item in rankings}
    top = rankings[0]
    selected = candidate_by_id[top.candidate_id]
    threshold: float
    improvement: float | None
    if goal is OptimizationGoal.LATENCY:
        threshold = policy.minimum_selection_improvement.latency_relative
        improvement = improvement_value(comparison_by_id[top.candidate_id], "p95_latency_seconds")
    elif goal is OptimizationGoal.THROUGHPUT:
        threshold = policy.minimum_selection_improvement.throughput_relative
        improvement = improvement_value(comparison_by_id[top.candidate_id], "requests_per_minute")
    elif goal is OptimizationGoal.MEMORY:
        threshold = policy.minimum_selection_improvement.memory_relative
        improvement = improvement_value(
            comparison_by_id[top.candidate_id], "measured_peak_rss_bytes"
        )
    else:
        threshold = policy.minimum_selection_improvement.balanced_score_absolute
        baseline_ranking = ranking_by_id.get(baseline.id)
        improvement = (
            top.score - baseline_ranking.score
            if top.score is not None
            and baseline_ranking is not None
            and baseline_ranking.score is not None
            else None
        )
    retain = selected.baseline or improvement is None or improvement < threshold
    if retain:
        baseline_ranking = ranking_by_id.get(baseline.id)
        return SelectionDecision(
            selection_id=selection_id,
            outcome=SelectionOutcome.BASELINE_RETAINED,
            selected_candidate_id=baseline.id,
            selected_candidate_hash=baseline.profile_hash,
            baseline_candidate_id=baseline.id,
            goal=goal,
            ranking_position=baseline_ranking.position if baseline_ranking else None,
            applicable_improvement=improvement,
            practical_improvement_threshold=threshold,
            reason_code="baseline_retained_no_meaningful_improvement",
            reason="Baseline retained because no candidate produced a meaningful improvement",
            screening_reference=screening_reference,
        )
    return SelectionDecision(
        selection_id=selection_id,
        outcome=SelectionOutcome.CANDIDATE_SELECTED,
        selected_candidate_id=selected.id,
        selected_candidate_hash=selected.profile_hash,
        baseline_candidate_id=baseline.id,
        goal=goal,
        ranking_position=top.position,
        applicable_improvement=improvement,
        practical_improvement_threshold=threshold,
        reason_code="candidate_exceeded_practical_improvement",
        reason="Selected profile for this machine, model, workload, and software version",
        screening_reference=screening_reference,
    )
