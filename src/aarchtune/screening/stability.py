"""Repetition aggregation and documented coefficient-of-variation stability."""

from __future__ import annotations

import statistics
from collections import defaultdict

from aarchtune.benchmark.statistics import numeric_statistics
from aarchtune.screening.models import (
    BenchExecutionResult,
    BenchSignature,
    NormalizedBenchMeasurement,
    ScenarioAggregate,
    ScreeningScenario,
    SignatureMembership,
    SignatureScreeningResult,
    SignatureStatus,
    StabilityAssessment,
    StabilityClass,
)


def assess_stability(
    values: list[float],
    *,
    failed_repetitions: int,
    timeout_count: int,
    stable_maximum: float,
    variable_maximum: float,
) -> StabilityAssessment:
    if len(values) < 2:
        return StabilityAssessment(
            measurement_count=len(values),
            failed_repetition_count=failed_repetitions,
            timeout_count=timeout_count,
            coefficient_of_variation=None,
            classification=StabilityClass.INSUFFICIENT_DATA,
        )
    mean = statistics.fmean(values)
    coefficient = statistics.stdev(values) / mean if mean > 0 else None
    if coefficient is None:
        classification = StabilityClass.INSUFFICIENT_DATA
    elif coefficient <= stable_maximum:
        classification = StabilityClass.STABLE
    elif coefficient <= variable_maximum:
        classification = StabilityClass.VARIABLE
    else:
        classification = StabilityClass.HIGHLY_VARIABLE
    return StabilityAssessment(
        measurement_count=len(values),
        failed_repetition_count=failed_repetitions,
        timeout_count=timeout_count,
        coefficient_of_variation=coefficient,
        classification=classification,
    )


def aggregate_signature(
    signature: BenchSignature,
    scenarios: list[ScreeningScenario],
    memberships: list[SignatureMembership],
    executions: list[BenchExecutionResult],
    measurements: list[NormalizedBenchMeasurement],
    *,
    repetitions: int,
    stable_maximum: float,
    variable_maximum: float,
) -> SignatureScreeningResult:
    signature_executions = [
        item for item in executions if item.command.signature_id == signature.id
    ]
    by_scenario: dict[str, list[BenchExecutionResult]] = defaultdict(list)
    for execution in signature_executions:
        by_scenario[execution.command.scenario_id].append(execution)
    aggregates: list[ScenarioAggregate] = []
    successful: list[str] = []
    failed: list[str] = []
    for scenario in scenarios:
        scenario_executions = by_scenario[scenario.id]
        valid_by_invocation: dict[str, float] = {}
        for measurement in measurements:
            if (
                measurement.signature_id == signature.id
                and measurement.scenario_id == scenario.id
                and measurement.provenance_valid
                and measurement.throughput_tokens_per_second.available
                and isinstance(measurement.throughput_tokens_per_second.value, (int, float))
                and not isinstance(measurement.throughput_tokens_per_second.value, bool)
            ):
                valid_by_invocation.setdefault(
                    measurement.invocation_id,
                    float(measurement.throughput_tokens_per_second.value),
                )
        values = list(valid_by_invocation.values())
        failure_count = repetitions - len(values)
        timeout_count = sum(item.timed_out for item in scenario_executions)
        stability = assess_stability(
            values,
            failed_repetitions=max(0, failure_count),
            timeout_count=timeout_count,
            stable_maximum=stable_maximum,
            variable_maximum=variable_maximum,
        )
        aggregate = ScenarioAggregate(
            signature_id=signature.id,
            scenario_id=scenario.id,
            metric_kind=scenario.metric_kind,
            throughput=numeric_statistics(values),
            stability=stability,
            successful_repetitions=len(values),
            failed_repetitions=max(0, failure_count),
        )
        aggregates.append(aggregate)
        if values and failure_count == 0:
            successful.append(scenario.id)
        else:
            failed.append(scenario.id)
    classes = [item.stability.classification for item in aggregates]
    if StabilityClass.HIGHLY_VARIABLE in classes:
        overall_class = StabilityClass.HIGHLY_VARIABLE
    elif StabilityClass.VARIABLE in classes:
        overall_class = StabilityClass.VARIABLE
    elif classes and all(item is StabilityClass.STABLE for item in classes):
        overall_class = StabilityClass.STABLE
    else:
        overall_class = StabilityClass.INSUFFICIENT_DATA
    all_values = [
        float(measurement.throughput_tokens_per_second.value)
        for measurement in measurements
        if measurement.signature_id == signature.id
        and measurement.provenance_valid
        and measurement.throughput_tokens_per_second.available
        and isinstance(measurement.throughput_tokens_per_second.value, (int, float))
        and not isinstance(measurement.throughput_tokens_per_second.value, bool)
    ]
    overall = assess_stability(
        all_values,
        failed_repetitions=sum(item.failed_repetitions for item in aggregates),
        timeout_count=sum(item.timed_out for item in signature_executions),
        stable_maximum=stable_maximum,
        variable_maximum=variable_maximum,
    ).model_copy(update={"classification": overall_class})
    peaks = []
    for execution in signature_executions:
        metric = execution.process_summary.whole_run_peak_rss_bytes
        if metric.available and isinstance(metric.value, int):
            peaks.append(metric.value)
    reasons: list[str] = []
    if failed:
        reasons.append("required_scenario_failed")
    if not all_values:
        reasons.append("no_valid_throughput")
    if overall_class is StabilityClass.HIGHLY_VARIABLE:
        reasons.append("unstable_measurements")
    eligible = not reasons
    if not signature.compatible:
        status = SignatureStatus.UNSUPPORTED
        eligible = False
        reasons.extend(signature.incompatibility_reasons)
    elif any(item.timed_out for item in signature_executions) and not successful:
        status = SignatureStatus.TIMED_OUT
    elif not successful:
        status = SignatureStatus.FAILED
    elif failed:
        status = SignatureStatus.PARTIAL
    elif overall_class is StabilityClass.HIGHLY_VARIABLE:
        status = SignatureStatus.UNSTABLE
    else:
        status = SignatureStatus.COMPLETED
    member_ids = sorted(
        item.candidate_id for item in memberships if item.bench_signature_id == signature.id
    )
    return SignatureScreeningResult(
        signature_id=signature.id,
        signature_hash=signature.signature_hash,
        status=status,
        supported_scenarios=[scenario.id for scenario in scenarios],
        successful_scenarios=successful,
        failed_scenarios=failed,
        scenario_aggregates=aggregates,
        process_peak_rss_bytes=max(peaks) if peaks else None,
        stability=overall,
        screening_eligible=eligible,
        reasons=reasons,
        member_candidate_ids=member_ids,
    )
