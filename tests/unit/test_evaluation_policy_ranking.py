from __future__ import annotations

from pathlib import Path

import pytest

from aarchtune.benchmark.models import (
    BaselineQualitySummary,
    OptionalMetric,
    ValidatorQualityStatistics,
    unavailable_metric,
)
from aarchtune.evaluation.comparison import compare_candidate, relative_improvement
from aarchtune.evaluation.drift import assess_drift
from aarchtune.evaluation.models import (
    CandidateConsistency,
    CandidateExecutionResult,
    CandidateExecutionStatus,
    CandidatePerformanceSummary,
    CandidateQualitySummary,
    DriftClassification,
    QualityGateStatus,
    ScreeningEvaluationReference,
    SelectionOutcome,
)
from aarchtune.evaluation.quality_gate import apply_quality_gate
from aarchtune.evaluation.quality_policy import load_quality_policy
from aarchtune.evaluation.ranking import rank_candidates
from aarchtune.evaluation.selection import select_profile
from aarchtune.optimization.models import CandidateProfile, OptimizationGoal, SearchPlan
from aarchtune.workload.schema import ValidatorType


def _profiles(plan_dir: Path) -> list[CandidateProfile]:
    plan = SearchPlan.model_validate_json((plan_dir / "search-plan.json").read_text())
    return plan.candidates[:3]


def _quality(
    run_id: str,
    *,
    rate: float = 1.0,
    json_rate: float = 1.0,
    validator_rate: float = 1.0,
    request_rate: float = 1.0,
    timeout_rate: float = 0.0,
    critical_failures: int = 0,
) -> CandidateQualitySummary:
    total = 10
    aggregate = BaselineQualitySummary(
        run_id=run_id,
        task_attempts=total,
        passed_task_attempts=round(rate * total),
        failed_task_attempts=total - round(rate * total),
        task_attempt_success_rate=rate,
        unique_tasks_passing_every_repetition=["a"] if rate == 1 else [],
        unique_tasks_failing_at_least_once=[] if rate == 1 else ["a"],
        validator_pass_count=round(validator_rate * 50),
        validator_failure_count=50 - round(validator_rate * 50),
        validator_pass_rate=validator_rate,
        json_valid_response_count=round(json_rate * total),
        json_validity_rate=json_rate,
        request_success_count=round(request_rate * total),
        request_success_rate=request_rate,
        timeout_count=round(timeout_rate * total),
        timeout_rate=timeout_rate,
        per_category={},
        per_validator_type={
            ValidatorType.REQUEST_SUCCEEDED: ValidatorQualityStatistics(
                total=10,
                passed=10 - critical_failures,
                failed=critical_failures,
                pass_rate=(10 - critical_failures) / 10,
            ),
            ValidatorType.NOT_CONTAINS_TEXT: ValidatorQualityStatistics(
                total=10,
                passed=10 - critical_failures,
                failed=critical_failures,
                pass_rate=(10 - critical_failures) / 10,
            ),
        },
    )
    return CandidateQualitySummary(
        aggregate=aggregate,
        consistency=CandidateConsistency(
            tasks_passing_every_repetition=5,
            tasks_failing_at_least_once=0 if rate == 1 else 1,
            inconsistent_task_count=0,
            per_task_pass_consistency_rate=1.0,
            latency_coefficient_of_variation=0.01,
            throughput_coefficient_of_variation=0.01,
        ),
    )


def _performance(
    *, rpm: float = 100.0, p95: float = 1.0, median: float = 0.8, rss: int = 100
) -> CandidatePerformanceSummary:
    return CandidatePerformanceSummary(
        configured_attempts=10,
        completed_attempts=10,
        successful_requests=10,
        request_success_rate=1.0,
        median_latency_seconds=median,
        p95_latency_seconds=p95,
        mean_latency_seconds=0.85,
        requests_per_minute=rpm,
        prompt_tokens_total=200,
        completion_tokens_total=80,
        server_prompt_throughput=2000.0,
        server_generation_throughput=500.0,
        measured_peak_rss_bytes=rss,
        whole_run_peak_rss_bytes=rss + 10,
        mean_measured_rss_bytes=float(rss - 10),
        mean_cpu_percent=50.0,
        peak_cpu_percent=90.0,
        time_to_first_token=unavailable_metric("non-streaming"),
        comparable=True,
        comparability_reasons=[],
    )


def _result(
    profile: CandidateProfile,
    *,
    performance: CandidatePerformanceSummary | None = None,
    quality: CandidateQualitySummary | None = None,
    status: CandidateExecutionStatus = CandidateExecutionStatus.COMPLETED,
) -> CandidateExecutionResult:
    return CandidateExecutionResult(
        candidate_id=profile.id,
        candidate_hash=profile.profile_hash,
        label=f"candidate-{profile.id}",
        profile=profile,
        status=status,
        run_id=profile.id,
        run_directory=Path("/tmp") / profile.id,
        screening_score=0.99,
        performance=performance,
        quality=quality,
        server_stopped=True,
        sampler_stopped=True,
    )


def _reference() -> ScreeningEvaluationReference:
    return ScreeningEvaluationReference(
        path=Path("/tmp/screening"),
        screening_id="screen",
        status="completed",
        manifest_sha256="a" * 64,
        plan_id="plan",
        plan_hash="b" * 64,
        goal=OptimizationGoal.BALANCED,
        advanced_candidate_count=3,
        synthetic_fixture=True,
    )


def test_policy_loads_strict_defaults_and_stable_hash() -> None:
    first = load_quality_policy(None)
    second = load_quality_policy(None)
    assert first.sha256 == second.sha256
    assert first.policy.absolute_minimums.task_success_rate == 0.95
    assert ValidatorType.REQUEST_SUCCEEDED in first.policy.critical_validator_types


def test_policy_rejects_unknown_fields(tmp_path: Path) -> None:
    policy = tmp_path / "bad.yaml"
    policy.write_text("schema_version: '1.0'\nunknown: true\n")
    with pytest.raises(Exception, match="Invalid quality policy"):
        load_quality_policy(policy)


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"rate": 0.9}, "absolute_quality_below_minimum"),
        ({"rate": 0.98}, "quality_regression"),
        ({"json_rate": 0.9}, "absolute_quality_below_minimum"),
        ({"validator_rate": 0.9}, "absolute_quality_below_minimum"),
        ({"request_rate": 0.9}, "absolute_quality_below_minimum"),
        ({"timeout_rate": 0.1}, "too_many_timeouts"),
        ({"critical_failures": 1}, "critical_validator_regression"),
    ],
)
def test_quality_gate_rejects_absolute_regression_timeout_and_critical(
    screen_plan_dir: Path, updates: dict[str, float | int], code: str
) -> None:
    baseline_profile, candidate_profile = _profiles(screen_plan_dir)[:2]
    baseline = _result(baseline_profile, performance=_performance(), quality=_quality("base"))
    candidate = _result(
        candidate_profile,
        performance=_performance(),
        quality=_quality("candidate", **updates),
    )
    decision = apply_quality_gate(candidate, baseline, load_quality_policy(None).policy, 2)
    assert decision.status is QualityGateStatus.FAILED
    assert code in {item.code for item in decision.violations}


def test_quality_gate_passes_and_records_inherited_baseline_failure(
    screen_plan_dir: Path,
) -> None:
    baseline_profile, candidate_profile = _profiles(screen_plan_dir)[:2]
    baseline = _result(
        baseline_profile,
        performance=_performance(),
        quality=_quality("base", critical_failures=1),
    )
    candidate = _result(
        candidate_profile,
        performance=_performance(),
        quality=_quality("candidate", critical_failures=1),
    )
    decision = apply_quality_gate(candidate, baseline, load_quality_policy(None).policy, 2)
    assert decision.status is QualityGateStatus.PASSED
    assert all(item.inherited_baseline_limitation for item in decision.critical_validator_decisions)


def test_quality_gate_infrastructure_and_insufficient_repetitions(
    screen_plan_dir: Path,
) -> None:
    baseline_profile, candidate_profile = _profiles(screen_plan_dir)[:2]
    baseline = _result(baseline_profile, performance=_performance(), quality=_quality("base"))
    failed = _result(candidate_profile, status=CandidateExecutionStatus.FAILED)
    assert (
        apply_quality_gate(failed, baseline, load_quality_policy(None).policy, 2).status
        is QualityGateStatus.INFRASTRUCTURE_FAILURE
    )
    candidate = _result(
        candidate_profile, performance=_performance(), quality=_quality("candidate")
    )
    assert (
        apply_quality_gate(candidate, baseline, load_quality_policy(None).policy, 1).status
        is QualityGateStatus.INSUFFICIENT_EVIDENCE
    )


def test_improvement_and_comparison_semantics(screen_plan_dir: Path) -> None:
    baseline_profile, candidate_profile = _profiles(screen_plan_dir)[:2]
    assert relative_improvement(100.0, 120.0, higher_is_better=True)[0] == pytest.approx(0.2)
    assert relative_improvement(100.0, 80.0, higher_is_better=False)[0] == pytest.approx(0.2)
    assert relative_improvement(0.0, 1.0, higher_is_better=True)[0] is None
    baseline = _result(baseline_profile, performance=_performance(), quality=_quality("base"))
    candidate = _result(
        candidate_profile,
        performance=_performance(rpm=120, p95=0.8, rss=80),
        quality=_quality("candidate"),
    )
    comparison = compare_candidate(candidate, baseline)
    assert comparison.comparable
    assert len(comparison.improvements) == 5
    assert candidate.performance.time_to_first_token.available is False


def test_drift_stable_warning_invalidating_and_unavailable(screen_plan_dir: Path) -> None:
    profile = _profiles(screen_plan_dir)[0]
    policy = load_quality_policy(None).policy
    start = _result(profile, performance=_performance(), quality=_quality("start"))
    stable = _result(profile, performance=_performance(rpm=99), quality=_quality("end"))
    warning = _result(profile, performance=_performance(rpm=60), quality=_quality("warn"))
    invalid = _result(profile, performance=_performance(), quality=_quality("bad", rate=0.8))
    failed = _result(profile, status=CandidateExecutionStatus.FAILED)
    assert assess_drift(start, stable, policy).classification is DriftClassification.STABLE
    assert assess_drift(start, warning, policy).classification is DriftClassification.WARNING
    assert assess_drift(start, invalid, policy).classification is DriftClassification.INVALIDATING
    assert assess_drift(start, failed, policy).classification is DriftClassification.INVALIDATING


@pytest.mark.parametrize("goal", list(OptimizationGoal))
def test_goal_rankings_exclude_failed_quality_and_ignore_screening_score(
    screen_plan_dir: Path, goal: OptimizationGoal
) -> None:
    profiles = _profiles(screen_plan_dir)
    results = [
        _result(
            profiles[0], performance=_performance(rpm=100, p95=1.0, rss=100), quality=_quality("a")
        ),
        _result(
            profiles[1], performance=_performance(rpm=130, p95=0.7, rss=120), quality=_quality("b")
        ),
        _result(
            profiles[2],
            performance=_performance(rpm=200, p95=0.5, rss=70),
            quality=_quality("c", rate=0.5),
        ),
    ]
    policy = load_quality_policy(None).policy
    decisions = [apply_quality_gate(item, results[0], policy, 2) for item in results]
    comparisons = [compare_candidate(item, results[0]) for item in results]
    ranked = rank_candidates(results, decisions, comparisons, goal, policy)
    assert profiles[2].id not in {item.candidate_id for item in ranked}
    assert [item.position for item in ranked] == list(range(1, len(ranked) + 1))


def test_selection_candidate_baseline_no_eligible_and_drift(screen_plan_dir: Path) -> None:
    profiles = _profiles(screen_plan_dir)
    policy = load_quality_policy(None).policy
    results = [
        _result(profiles[0], performance=_performance(), quality=_quality("a")),
        _result(
            profiles[1], performance=_performance(rpm=150, p95=0.6, rss=80), quality=_quality("b")
        ),
    ]
    decisions = [apply_quality_gate(item, results[0], policy, 2) for item in results]
    comparisons = [compare_candidate(item, results[0]) for item in results]
    rankings = rank_candidates(results, decisions, comparisons, OptimizationGoal.BALANCED, policy)
    stable = assess_drift(results[0], results[0], policy)
    chosen = select_profile(
        rankings=rankings,
        comparisons=comparisons,
        candidates=profiles[:2],
        baseline=profiles[0],
        goal=OptimizationGoal.BALANCED,
        policy=policy,
        drift=stable,
        screening_reference=_reference(),
    )
    assert chosen.outcome is SelectionOutcome.CANDIDATE_SELECTED
    none = select_profile(
        rankings=[],
        comparisons=[],
        candidates=profiles,
        baseline=profiles[0],
        goal=OptimizationGoal.BALANCED,
        policy=policy,
        drift=stable,
        screening_reference=_reference(),
    )
    assert none.outcome is SelectionOutcome.NO_ELIGIBLE_CANDIDATE
    invalid_drift = assess_drift(
        results[0],
        _result(profiles[0], performance=_performance(), quality=_quality("bad", rate=0.5)),
        policy,
    )
    invalid = select_profile(
        rankings=rankings,
        comparisons=comparisons,
        candidates=profiles[:2],
        baseline=profiles[0],
        goal=OptimizationGoal.BALANCED,
        policy=policy,
        drift=invalid_drift,
        screening_reference=_reference(),
    )
    assert invalid.outcome is SelectionOutcome.INVALIDATED_BY_DRIFT


def test_optional_metric_model_does_not_turn_unavailable_into_zero() -> None:
    metric = OptionalMetric(value=None, available=False, reason="missing")
    assert metric.value is None
