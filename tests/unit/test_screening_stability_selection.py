from __future__ import annotations

from pathlib import Path

import pytest

from aarchtune.benchmark.statistics import numeric_statistics
from aarchtune.optimization.models import OptimizationGoal, SearchPlan
from aarchtune.screening.models import (
    DecisionStatus,
    LlamaBenchCapabilities,
    MetricKind,
    ScenarioAggregate,
    SignatureScreeningResult,
    SignatureStatus,
    StabilityAssessment,
    StabilityClass,
)
from aarchtune.screening.selection import score_signatures, select_candidates
from aarchtune.screening.signatures import build_signatures
from aarchtune.screening.stability import assess_stability


@pytest.mark.parametrize(
    ("values", "classification"),
    [
        ([100.0], StabilityClass.INSUFFICIENT_DATA),
        ([100.0, 101.0, 99.0], StabilityClass.STABLE),
        ([100.0, 105.0, 95.0], StabilityClass.VARIABLE),
        ([100.0, 140.0, 60.0], StabilityClass.HIGHLY_VARIABLE),
    ],
)
def test_stability_classification(values: list[float], classification: StabilityClass) -> None:
    result = assess_stability(
        values,
        failed_repetitions=1,
        timeout_count=1,
        stable_maximum=0.03,
        variable_maximum=0.10,
    )
    assert result.classification is classification
    assert result.failed_repetition_count == 1
    assert result.timeout_count == 1


def _result(
    signature_id: str,
    signature_hash: str,
    value: float | None,
    *,
    eligible: bool = True,
    peak: int | None = 100,
) -> SignatureScreeningResult:
    aggregates = []
    for scenario, kind, multiplier in (
        ("prefill", MetricKind.PREFILL, 2.0),
        ("decode", MetricKind.DECODE, 1.0),
        ("mixed", MetricKind.COMBINED, 1.5),
    ):
        values = [] if value is None else [value * multiplier]
        aggregates.append(
            ScenarioAggregate(
                signature_id=signature_id,
                scenario_id=scenario,
                metric_kind=kind,
                throughput=numeric_statistics(values),
                stability=StabilityAssessment(
                    measurement_count=len(values),
                    failed_repetition_count=0 if values else 1,
                    timeout_count=0,
                    coefficient_of_variation=None,
                    classification=StabilityClass.INSUFFICIENT_DATA,
                ),
                successful_repetitions=len(values),
                failed_repetitions=0 if values else 1,
            )
        )
    return SignatureScreeningResult(
        signature_id=signature_id,
        signature_hash=signature_hash,
        status=SignatureStatus.COMPLETED if eligible else SignatureStatus.FAILED,
        supported_scenarios=["prefill", "decode", "mixed"],
        successful_scenarios=["prefill", "decode", "mixed"] if eligible else [],
        failed_scenarios=[] if eligible else ["decode"],
        scenario_aggregates=aggregates,
        process_peak_rss_bytes=peak,
        stability=StabilityAssessment(
            measurement_count=3 if value is not None else 0,
            failed_repetition_count=0,
            timeout_count=0,
            coefficient_of_variation=0.0 if value is not None else None,
            classification=StabilityClass.STABLE
            if value is not None
            else StabilityClass.INSUFFICIENT_DATA,
        ),
        screening_eligible=eligible,
        reasons=[] if eligible else ["required_scenario_failed"],
        member_candidate_ids=[],
    )


@pytest.mark.parametrize("goal", list(OptimizationGoal))
def test_goal_scores_are_transparent_and_bounded(goal: OptimizationGoal) -> None:
    results = [
        _result("a", "a" * 64, 100.0, peak=100),
        _result("b", "b" * 64, 200.0, peak=200),
    ]
    scored = score_signatures(results, goal)
    assert all(item.score is not None and 0 <= item.score <= 1 for item in scored)
    assert all(item.score_components for item in scored)
    for item in scored:
        contributions = [
            component.contribution
            for component in item.score_components
            if component.contribution is not None
        ]
        assert sum(contributions) == pytest.approx(item.score or 0.0)


def test_missing_components_renormalize_and_no_metrics_stays_ineligible() -> None:
    available = _result("a", "a" * 64, 100.0, peak=None)
    unavailable = _result("b", "b" * 64, None, eligible=False, peak=None)
    scored = score_signatures([available, unavailable], OptimizationGoal.BALANCED)
    assert scored[0].score is not None
    rss = next(item for item in scored[0].score_components if item.component == "inverse_peak_rss")
    assert rss.available is False
    assert scored[1].score is None


def test_advancement_retains_baseline_is_bounded_diverse_and_deterministic(
    screen_plan_dir: Path, bench_capabilities: LlamaBenchCapabilities
) -> None:
    plan = SearchPlan.model_validate_json((screen_plan_dir / "search-plan.json").read_text())
    signatures, memberships = build_signatures(plan.candidates, bench_capabilities)
    results = score_signatures(
        [
            _result(signature.id, signature.signature_hash, float(index + 1))
            for index, signature in enumerate(signatures)
        ],
        plan.goal,
    )
    first = select_candidates(plan.candidates, memberships, results, 4)
    second = select_candidates(plan.candidates, memberships, results, 4)
    assert first == second
    advanced, decisions = first
    assert len(advanced) == 4
    assert advanced[0].id == "baseline"
    assert len({item.runtime.threads for item in advanced}) >= 2
    assert len({item.runtime.prompt_cache for item in advanced}) >= 2
    assert len({item.runtime.parallel_slots for item in advanced}) >= 2
    assert len(decisions) == len(plan.candidates)
    assert sum(item.decision is DecisionStatus.ADVANCED for item in decisions) == 4


def test_failed_signature_is_never_advanced(
    screen_plan_dir: Path, bench_capabilities: LlamaBenchCapabilities
) -> None:
    plan = SearchPlan.model_validate_json((screen_plan_dir / "search-plan.json").read_text())
    signatures, memberships = build_signatures(plan.candidates, bench_capabilities)
    results = [
        _result(
            signature.id,
            signature.signature_hash,
            100.0,
            eligible=index != 0,
        )
        for index, signature in enumerate(signatures)
    ]
    advanced, decisions = select_candidates(plan.candidates, memberships, results, 6)
    failed_id = signatures[0].id
    assert all(
        next(item for item in memberships if item.candidate_id == candidate.id).bench_signature_id
        != failed_id
        for candidate in advanced
    )
    assert any(
        item.signature_id == failed_id and item.decision is DecisionStatus.SCREENING_FAILED
        for item in decisions
    )
