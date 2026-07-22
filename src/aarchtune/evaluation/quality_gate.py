"""Absolute, baseline-relative, evidence, and critical-validator quality gates."""

from __future__ import annotations

from aarchtune.evaluation.models import (
    CandidateExecutionResult,
    CriticalValidatorDecision,
    QualityDecision,
    QualityGateStatus,
    QualityMetricDecision,
    QualityPolicy,
    QualityViolation,
)


def _quality_values(result: CandidateExecutionResult) -> dict[str, float | None]:
    quality = result.quality.aggregate if result.quality else None
    return {
        "request_success_rate": quality.request_success_rate if quality else None,
        "task_success_rate": quality.task_attempt_success_rate if quality else None,
        "json_validity_rate": quality.json_validity_rate if quality else None,
        "validator_pass_rate": quality.validator_pass_rate if quality else None,
        "timeout_rate": quality.timeout_rate if quality else None,
    }


def apply_quality_gate(
    candidate: CandidateExecutionResult,
    baseline: CandidateExecutionResult,
    policy: QualityPolicy,
    repetitions: int,
) -> QualityDecision:
    if candidate.status.value != "completed" or candidate.performance is None:
        return QualityDecision(
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            status=QualityGateStatus.INFRASTRUCTURE_FAILURE,
            metric_decisions=[],
            critical_validator_decisions=[],
            violations=[
                QualityViolation(
                    code="request_infrastructure_failure",
                    metric=None,
                    reason=candidate.failure_message or "Candidate execution did not complete",
                )
            ],
            completed_attempt_fraction=None,
            observed_repetitions_per_task=None,
        )
    baseline_values = _quality_values(baseline)
    candidate_values = _quality_values(candidate)
    decisions: list[QualityMetricDecision] = []
    violations: list[QualityViolation] = []
    for metric in (
        "request_success_rate",
        "task_success_rate",
        "json_validity_rate",
        "validator_pass_rate",
    ):
        baseline_value = baseline_values[metric]
        candidate_value = candidate_values[metric]
        absolute = getattr(policy.absolute_minimums, metric)
        regression_limit = getattr(policy.maximum_regression_from_baseline, metric)
        observed = (
            baseline_value - candidate_value
            if baseline_value is not None and candidate_value is not None
            else None
        )
        absolute_passed = candidate_value is not None and candidate_value >= absolute
        regression_passed = observed is not None and observed <= regression_limit
        passed = absolute_passed and regression_passed
        if candidate_value is None or baseline_value is None:
            reason = "Metric is unavailable"
        elif not absolute_passed and not regression_passed:
            reason = (
                f"Candidate {metric} is below absolute minimum {absolute:.4f} and regressed "
                f"by {observed:.4f} absolute rate points (0.01 = one percentage point); "
                f"regression limit is {regression_limit:.4f}"
            )
        elif not absolute_passed:
            reason = f"Candidate {metric} is below absolute minimum {absolute:.4f}"
        elif not regression_passed:
            reason = (
                f"Candidate {metric} regressed by {observed:.4f} absolute rate points "
                f"(0.01 = one percentage point); limit is {regression_limit:.4f}"
            )
        else:
            reason = "Absolute and baseline-relative requirements passed"
        decisions.append(
            QualityMetricDecision(
                metric=metric,
                baseline_value=baseline_value,
                candidate_value=candidate_value,
                absolute_threshold=absolute,
                regression_limit=regression_limit,
                observed_regression=observed,
                passed=passed,
                reason=reason,
            )
        )
        if not passed:
            if candidate_value is None or baseline_value is None:
                violations.append(
                    QualityViolation(
                        code="insufficient_completed_attempts", metric=metric, reason=reason
                    )
                )
            else:
                if not absolute_passed:
                    violations.append(
                        QualityViolation(
                            code="absolute_quality_below_minimum",
                            metric=metric,
                            reason=f"Candidate {metric} is below absolute minimum {absolute:.4f}",
                        )
                    )
                if not regression_passed:
                    violations.append(
                        QualityViolation(
                            code="quality_regression",
                            metric=metric,
                            reason=(
                                f"Candidate {metric} regressed by {observed:.4f} absolute "
                                f"rate points; limit is {regression_limit:.4f}"
                            ),
                        )
                    )
    timeout = candidate_values["timeout_rate"]
    timeout_passed = timeout is not None and timeout <= policy.maximums.timeout_rate
    decisions.append(
        QualityMetricDecision(
            metric="timeout_rate",
            baseline_value=baseline_values["timeout_rate"],
            candidate_value=timeout,
            absolute_threshold=policy.maximums.timeout_rate,
            regression_limit=None,
            observed_regression=None,
            passed=timeout_passed,
            reason=(
                "Timeout rate is within the maximum"
                if timeout_passed
                else "Timeout rate is unavailable or exceeds the maximum"
            ),
        )
    )
    if not timeout_passed:
        violations.append(
            QualityViolation(
                code="too_many_timeouts",
                metric="timeout_rate",
                reason="Timeout rate is unavailable or exceeds policy",
            )
        )
    configured = candidate.performance.configured_attempts
    fraction = candidate.performance.completed_attempts / configured if configured > 0 else None
    if fraction is None or fraction < policy.minimum_evidence.completed_attempt_fraction:
        violations.append(
            QualityViolation(
                code="insufficient_completed_attempts",
                metric="completed_attempt_fraction",
                reason="Completed attempt fraction is below policy",
            )
        )
    if repetitions < policy.minimum_evidence.repetitions_per_task:
        violations.append(
            QualityViolation(
                code="insufficient_repetitions",
                metric="repetitions_per_task",
                reason="Configured repetitions are below policy",
            )
        )
    critical: list[CriticalValidatorDecision] = []
    baseline_by_type = baseline.quality.aggregate.per_validator_type if baseline.quality else {}
    candidate_by_type = candidate.quality.aggregate.per_validator_type if candidate.quality else {}
    for validator_type in policy.critical_validator_types:
        baseline_stats = baseline_by_type.get(validator_type)
        candidate_stats = candidate_by_type.get(validator_type)
        baseline_failures = baseline_stats.failed if baseline_stats else 0
        candidate_failures = candidate_stats.failed if candidate_stats else 0
        baseline_rate = (
            baseline_failures / baseline_stats.total
            if baseline_stats is not None and baseline_stats.total
            else None
        )
        candidate_rate = (
            candidate_failures / candidate_stats.total
            if candidate_stats is not None and candidate_stats.total
            else None
        )
        passed = (
            baseline_rate is not None
            and candidate_rate is not None
            and candidate_rate <= baseline_rate
            and candidate_failures <= baseline_failures
        )
        reason = (
            "Critical validator did not add failures"
            if passed
            else "Critical validator failure count or rate regressed"
        )
        critical.append(
            CriticalValidatorDecision(
                validator_type=validator_type,
                baseline_failures=baseline_failures,
                candidate_failures=candidate_failures,
                baseline_failure_rate=baseline_rate,
                candidate_failure_rate=candidate_rate,
                passed=passed,
                inherited_baseline_limitation=baseline_failures > 0,
                reason=reason,
            )
        )
        if not passed:
            violations.append(
                QualityViolation(
                    code="critical_validator_regression",
                    metric=validator_type.value,
                    reason=reason,
                )
            )
    insufficient_codes = {"insufficient_completed_attempts", "insufficient_repetitions"}
    status = (
        QualityGateStatus.INSUFFICIENT_EVIDENCE
        if any(item.code in insufficient_codes for item in violations)
        else QualityGateStatus.FAILED
        if violations
        else QualityGateStatus.PASSED
    )
    return QualityDecision(
        candidate_id=candidate.candidate_id,
        candidate_hash=candidate.candidate_hash,
        status=status,
        metric_decisions=decisions,
        critical_validator_decisions=critical,
        violations=violations,
        completed_attempt_fraction=fraction,
        observed_repetitions_per_task=repetitions,
    )
