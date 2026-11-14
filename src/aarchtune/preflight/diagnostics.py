"""Assess complete baseline evidence against absolute quality requirements."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from aarchtune.baseline.models import BaselineManifest, BaselineSummary, RunStatus
from aarchtune.benchmark.models import RawAttemptRecord, RequestMeasurement, TaskAttemptResult
from aarchtune.benchmark.request_metrics import aggregate_quality
from aarchtune.evaluation.models import QualityPolicySource
from aarchtune.preflight.models import PolicyCheck, PreflightReport, TaskDiagnosis
from aarchtune.workload.schema import ValidatorType


def _diagnose(records: list[RawAttemptRecord]) -> list[TaskDiagnosis]:
    grouped: dict[str, list[RawAttemptRecord]] = defaultdict(list)
    for record in records:
        grouped[record.task_id].append(record)
    tasks: list[TaskDiagnosis] = []
    for task_id, attempts in sorted(grouped.items()):
        failures: Counter[str] = Counter()
        for attempt in attempts:
            execution = attempt.execution
            if not execution.request_succeeded or execution.timed_out:
                failures["timeout" if execution.timed_out else "request_failed"] += 1
                continue
            results = attempt.validation.validator_results
            failed = {result.validator for result in results if not result.passed}
            if ValidatorType.VALID_JSON in failed:
                failures["malformed_json"] += 1
                continue
            structural = failed & {ValidatorType.JSON_SCHEMA, ValidatorType.REQUIRED_FIELDS}
            if structural:
                failures["schema_or_fields"] += 1
            # Parsing/shape failures also fail exact-value checks. Do not call those
            # independent semantic errors without a successfully parsed JSON value.
            json_passed = any(
                result.passed
                and result.validator in {ValidatorType.VALID_JSON, ValidatorType.JSON_SCHEMA}
                for result in results
            )
            if (
                json_passed
                and not structural
                and failed & {ValidatorType.EXACT_VALUE, ValidatorType.ALLOWED_VALUE}
            ):
                failures["incorrect_value"] += 1
            if ValidatorType.NOT_CONTAINS_TEXT in failed:
                failures["forbidden_content"] += 1
            if failed - {
                ValidatorType.VALID_JSON,
                ValidatorType.JSON_SCHEMA,
                ValidatorType.REQUIRED_FIELDS,
                ValidatorType.EXACT_VALUE,
                ValidatorType.ALLOWED_VALUE,
                ValidatorType.NOT_CONTAINS_TEXT,
            }:
                failures["other_validator"] += 1
        tasks.append(
            TaskDiagnosis(
                task_id=task_id,
                attempts=len(attempts),
                passed=sum(attempt.validation.passed is True for attempt in attempts),
                failures=dict(failures),
            )
        )
    return tasks


def diagnose_baseline(baseline_dir: Path, source: QualityPolicySource) -> PreflightReport:
    """Check persisted evidence, never executing or changing the runtime."""

    root = baseline_dir.expanduser().resolve()
    errors: list[str] = []
    checks: list[PolicyCheck] = []
    records: list[RawAttemptRecord] = []
    summary: BaselineSummary | None = None
    manifest: BaselineManifest | None = None
    evidence_hashes: dict[str, str] = {}
    filenames = (
        "manifest.json",
        "baseline-summary.json",
        "raw-attempts.jsonl",
        "request-metrics.jsonl",
    )
    try:
        contents: dict[str, bytes] = {}
        for name in filenames:
            contents[name] = (root / name).read_bytes()
            evidence_hashes[name] = hashlib.sha256(contents[name]).hexdigest()
        manifest = BaselineManifest.model_validate_json(contents["manifest.json"])
        summary = BaselineSummary.model_validate_json(contents["baseline-summary.json"])
        records = [
            RawAttemptRecord.model_validate_json(line)
            for line in contents["raw-attempts.jsonl"].splitlines()
            if line.strip()
        ]
        measurements = [
            RequestMeasurement.model_validate_json(line)
            for line in contents["request-metrics.jsonl"].splitlines()
            if line.strip()
        ]
        if manifest.status is not RunStatus.COMPLETED or summary.status is not RunStatus.COMPLETED:
            errors.append("Baseline execution is incomplete.")
        if manifest.server_stopped is not True or manifest.sampler_stopped is not True:
            errors.append("Baseline process and sampler cleanup is unconfirmed.")
        response_type = summary.execution.response_format_mode.response_type
        if (
            summary.execution.response_format_type != response_type
            or summary.execution.response_format_applied != (response_type is not None)
        ):
            errors.append("Baseline response-format provenance is inconsistent with its mode.")
        expected = summary.workload_task_count * summary.repetitions
        if expected <= 0 or summary.benchmark.total_configured_attempts != expected:
            errors.append("Configured attempts do not match workload size and repetitions.")
        if not (
            len(records)
            == len(measurements)
            == manifest.completed_attempt_count
            == summary.benchmark.measured_attempts_completed
        ):
            errors.append("Raw, request, manifest, and summary attempt counts disagree.")
        ids = [record.attempt_id for record in records]
        if len(ids) != len(set(ids)) or ids != [item.attempt_id for item in measurements]:
            errors.append("Attempt IDs are duplicated or request evidence is mismatched.")
        task_counts = Counter(record.task_id for record in records)
        if len(task_counts) != summary.workload_task_count:
            errors.append("Evidence does not cover every workload task.")
        slots = {(record.task_id, record.repetition) for record in records}
        if len(slots) != len(records) or any(
            record.repetition < 1 or record.repetition > summary.repetitions for record in records
        ):
            errors.append("Per-task repetition evidence is duplicated or out of range.")
        attempts: list[TaskAttemptResult] = []
        for record, measurement in zip(records, measurements, strict=True):
            if (
                record.run_id != summary.run_id
                or measurement.run_id != summary.run_id
                or record.task_id != measurement.task_id
                or record.validation.task_id != record.task_id
                or record.repetition != measurement.repetition
                or record.execution != measurement.execution
            ):
                errors.append("Attempt identity or execution metadata disagrees with the baseline.")
                break
            request = measurement.request
            if (
                request.response_format_mode.value == "json_schema"
                and request.response_format_schema is None
            ):
                errors.append("Schema-mode request is missing its generation schema.")
                break
            if (
                request.response_format_mode != summary.execution.response_format_mode
                or request.response_format_applied != summary.execution.response_format_applied
                or request.response_format_type != summary.execution.response_format_type
            ):
                errors.append("Request response-format provenance disagrees with the baseline.")
                break
            attempts.append(TaskAttemptResult(raw=record, measurement=measurement))
        if (
            manifest.run_id != summary.run_id
            or aggregate_quality(summary.run_id, attempts) != summary.quality
        ):
            errors.append("Quality summary does not match raw validation evidence.")
        policy = source.policy

        def check(
            name: str, observed: float | None, threshold: float, *, maximum: bool = False
        ) -> None:
            checks.append(
                PolicyCheck(
                    name=name,
                    observed=observed,
                    threshold=threshold,
                    comparison="maximum" if maximum else "minimum",
                    passed=observed is not None
                    and (observed <= threshold if maximum else observed >= threshold),
                )
            )

        for name, value in (
            ("request_success_rate", summary.quality.request_success_rate),
            ("task_success_rate", summary.quality.task_attempt_success_rate),
            ("json_validity_rate", summary.quality.json_validity_rate),
            ("validator_pass_rate", summary.quality.validator_pass_rate),
        ):
            check(name, value, getattr(policy.absolute_minimums, name))
        check(
            "timeout_rate", summary.quality.timeout_rate, policy.maximums.timeout_rate, maximum=True
        )
        configured = summary.benchmark.total_configured_attempts
        check(
            "completed_attempt_fraction",
            len(records) / configured if configured else None,
            policy.minimum_evidence.completed_attempt_fraction,
        )
        check(
            "repetitions_per_task",
            float(min(task_counts.values(), default=0)),
            float(policy.minimum_evidence.repetitions_per_task),
        )
        for validator_type in policy.critical_validator_types:
            stats = summary.quality.per_validator_type.get(validator_type)
            check(
                f"critical_validator:{validator_type.value}",
                float(stats.failed) if stats and stats.total else None,
                0.0,
                maximum=True,
            )
    except (OSError, ValueError) as exc:
        # ValidationError text can embed response_text from a malformed raw
        # record. Diagnostics expose the failure kind without copying that data.
        errors.append(f"Cannot validate baseline evidence ({type(exc).__name__}).")
        if manifest is not None and manifest.error_type:
            errors.append(f"Baseline execution failure type: {manifest.error_type}.")

    tasks = _diagnose(records)
    categories = {name for task in tasks for name in task.failures}
    recommendations: list[str] = []
    if errors:
        recommendations.append(
            "Repair or rerun the baseline to obtain complete, consistent evidence."
        )
    if manifest is not None and manifest.error_type == "ResponseFormatUnsupportedError":
        recommendations.append(
            "The server rejected the requested output format. Check support in this "
            "llama.cpp build and simplify unsupported schemas; no fallback was applied."
        )
    if categories & {"request_failed", "timeout"}:
        recommendations.append(
            "Check server logs, model memory requirements, and request timeouts."
        )
    if "malformed_json" in categories:
        recommendations.append(
            "Check the chat template and token limit; try supported schema-constrained output, "
            "then repeat baseline validation."
        )
    if "schema_or_fields" in categories:
        recommendations.append(
            "Align the output schema and prompt with the workload's required fields."
        )
    if "incorrect_value" in categories:
        recommendations.append(
            "Parsed outputs contain incorrect values. Review task instructions or try a more "
            "capable model; structured output alone does not establish correct decisions."
        )
    if "forbidden_content" in categories:
        recommendations.append(
            "Investigate forbidden content failures before deploying this workload."
        )
    insufficient = errors or any(
        not item.passed
        and (
            item.observed is None
            or item.name in {"completed_attempt_fraction", "repetitions_per_task"}
        )
        for item in checks
    )
    outcome: Literal["evidence_incomplete", "quality_failed", "quality_passed"] = (
        "evidence_incomplete"
        if insufficient
        else "quality_failed"
        if any(not item.passed for item in checks)
        else "quality_passed"
    )
    if outcome == "evidence_incomplete" and not errors:
        recommendations.append(
            "Increase baseline repetitions or add missing policy-required validators."
        )
    if outcome == "quality_passed":
        recommendations.append(
            "Baseline quality passed. Proceed to tuning and validate every candidate."
        )
    else:
        recommendations.append(
            "Resolve baseline quality or evidence failures before searching runtime settings."
        )
    return PreflightReport(
        baseline_dir=root,
        baseline_run_id=summary.run_id if summary else None,
        policy_sha256=source.sha256,
        evidence_sha256=evidence_hashes,
        outcome=outcome,
        can_tune=outcome == "quality_passed",
        synthetic=summary.synthetic_fixture if summary else False,
        is_arm64=summary.is_arm64 if summary else False,
        response_format=summary.execution.response_format_mode.value if summary else None,
        checks=checks,
        evidence_errors=errors,
        tasks=tasks,
        recommendations=recommendations,
    )
