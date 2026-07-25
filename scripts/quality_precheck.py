#!/usr/bin/env python3
"""Sanitize one baseline-only model quality precheck without retaining responses."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from aarchtune.baseline.models import BaselineManifest, BaselineSummary
from aarchtune.evaluation.quality_policy import load_quality_policy
from aarchtune.orchestration.stages import validate_baseline

EXPECTED_TASKS = 5
EXPECTED_REPETITIONS = 2
EXPECTED_ATTEMPTS = EXPECTED_TASKS * EXPECTED_REPETITIONS

_CLASSIFICATION_ORDER = (
    "request_failed",
    "timeout",
    "valid_json",
    "malformed_json",
    "missing_required_field",
    "wrong_schema",
    "wrong_allowed_value",
    "forbidden_text_present",
)


def verify_model(path: Path, expected_size: int, expected_sha256: str) -> dict[str, Any]:
    """Verify an immutable model blob before it can be used for inference."""

    if not path.is_file():
        raise ValueError(f"model file does not exist: {path}")
    size = path.stat().st_size
    if size != expected_size:
        raise ValueError(f"model size mismatch: expected {expected_size}, observed {size}")
    hasher = hashlib.sha256()
    with path.open("rb") as model:
        for chunk in iter(lambda: model.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"model SHA-256 mismatch: expected {expected_sha256}, observed {digest}")
    return {"size_bytes": size, "sha256": digest, "verified": True}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _validator_results(record: dict[str, Any]) -> list[dict[str, Any]]:
    validation = record.get("validation")
    if not isinstance(validation, dict):
        return []
    results = validation.get("validator_results")
    return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []


def classify_attempt(record: dict[str, Any]) -> dict[str, Any]:
    """Return classifications and validator names without response content or observations."""

    execution = record.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    results = _validator_results(record)
    by_name = {
        item.get("validator"): item.get("passed")
        for item in results
        if isinstance(item.get("validator"), str)
    }
    classifications: set[str] = set()
    if execution.get("request_succeeded") is not True:
        classifications.add("request_failed")
    if execution.get("timed_out") is True:
        classifications.add("timeout")
    if by_name.get("valid_json") is True:
        classifications.add("valid_json")
    elif "valid_json" in by_name:
        classifications.add("malformed_json")
    if by_name.get("required_fields") is False:
        classifications.add("missing_required_field")
    if by_name.get("json_schema") is False:
        classifications.add("wrong_schema")
    if by_name.get("allowed_value") is False or by_name.get("exact_value") is False:
        classifications.add("wrong_allowed_value")
    if by_name.get("not_contains_text") is False:
        classifications.add("forbidden_text_present")
    failed_validators = sorted(
        name for name, passed in by_name.items() if passed is False and isinstance(name, str)
    )
    return {
        "attempt_id": record.get("attempt_id"),
        "repetition": record.get("repetition"),
        "classifications": [item for item in _CLASSIFICATION_ORDER if item in classifications],
        "failed_validators": failed_validators,
        "task_passed": (
            record.get("validation", {}).get("passed")
            if isinstance(record.get("validation"), dict)
            else None
        ),
    }


def _evidence_errors(
    baseline_dir: Path,
    summary: BaselineSummary,
    records: list[dict[str, Any]],
    minimum_fraction: float,
    minimum_repetitions: int,
) -> list[str]:
    errors: list[str] = []
    valid, reason = validate_baseline(baseline_dir)
    if not valid:
        errors.append(reason or "existing baseline validation failed")
    benchmark = summary.benchmark
    if benchmark.total_configured_attempts != EXPECTED_ATTEMPTS:
        errors.append("configured attempt count is not 10")
    if benchmark.measured_attempts_completed != EXPECTED_ATTEMPTS:
        errors.append("completed attempt count is not 10")
    fraction = (
        benchmark.measured_attempts_completed / benchmark.total_configured_attempts
        if benchmark.total_configured_attempts
        else 0.0
    )
    if fraction < minimum_fraction:
        errors.append("completed-attempt fraction is below the existing quality policy")
    if summary.repetitions < minimum_repetitions:
        errors.append("repetitions per task are below the existing quality policy")
    if len(records) != EXPECTED_ATTEMPTS:
        errors.append("raw validation record count is not 10")
    attempt_ids = [item.get("attempt_id") for item in records]
    if len(set(attempt_ids)) != len(attempt_ids):
        errors.append("attempt IDs are not unique")
    task_counts = Counter(item.get("task_id") for item in records)
    if len(task_counts) != EXPECTED_TASKS or any(
        count != EXPECTED_REPETITIONS for count in task_counts.values()
    ):
        errors.append("per-task repetition evidence is incomplete")
    return errors


def sanitize_baseline(
    baseline_dir: Path,
    policy_path: Path,
    output_dir: Path,
) -> str:
    """Validate, classify, and write only response-free quality evidence."""

    output_dir.mkdir(parents=True, exist_ok=True)
    policy_source = load_quality_policy(policy_path)
    summary = BaselineSummary.model_validate_json(
        (baseline_dir / "baseline-summary.json").read_text(encoding="utf-8")
    )
    manifest = BaselineManifest.model_validate_json(
        (baseline_dir / "manifest.json").read_text(encoding="utf-8")
    )
    records = [
        json.loads(line)
        for line in (baseline_dir / "raw-attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("raw validation evidence must contain JSON objects")

    policy = policy_source.policy
    evidence_errors = _evidence_errors(
        baseline_dir,
        summary,
        records,
        policy.minimum_evidence.completed_attempt_fraction,
        policy.minimum_evidence.repetitions_per_task,
    )
    quality = summary.quality
    aggregate = {
        "configured_attempts": summary.benchmark.total_configured_attempts,
        "completed_attempts": summary.benchmark.measured_attempts_completed,
        "completed_attempt_fraction": (
            summary.benchmark.measured_attempts_completed
            / summary.benchmark.total_configured_attempts
            if summary.benchmark.total_configured_attempts
            else None
        ),
        "request_success_rate": quality.request_success_rate,
        "task_success_rate": quality.task_attempt_success_rate,
        "json_validity_rate": quality.json_validity_rate,
        "validator_pass_rate": quality.validator_pass_rate,
        "timeout_rate": quality.timeout_rate,
    }
    metric_failures: list[str] = []
    for name in (
        "request_success_rate",
        "task_success_rate",
        "json_validity_rate",
        "validator_pass_rate",
    ):
        value = aggregate[name]
        threshold = getattr(policy.absolute_minimums, name)
        if value is None or value < threshold:
            metric_failures.append(name)
    if quality.timeout_rate is None or quality.timeout_rate > policy.maximums.timeout_rate:
        metric_failures.append("timeout_rate")
    for validator_type in policy.critical_validator_types:
        statistics = quality.per_validator_type.get(validator_type)
        if statistics is None or statistics.failed:
            metric_failures.append(f"critical_validator:{validator_type.value}")

    if evidence_errors:
        outcome = "quality_evidence_incomplete"
    elif metric_failures:
        outcome = "quality_policy_failed"
    else:
        outcome = "quality_policy_passed"

    per_task: list[dict[str, Any]] = []
    for task_id in sorted({str(item.get("task_id")) for item in records}):
        attempts = [classify_attempt(item) for item in records if item.get("task_id") == task_id]
        per_task.append({"task_id": task_id, "attempts": attempts})

    sanitized_manifest = {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "status": manifest.status.value,
        "stage": manifest.stage.value,
        "completed_attempt_count": manifest.completed_attempt_count,
        "server_stopped": manifest.server_stopped,
        "sampler_stopped": manifest.sampler_stopped,
    }
    policy_outcome = {
        "outcome": outcome,
        "policy_sha256": policy_source.sha256,
        "aggregate": aggregate,
        "evidence_errors": evidence_errors,
        "failed_policy_checks": sorted(set(metric_failures)),
        "thresholds": {
            "absolute_minimums": policy.absolute_minimums.model_dump(mode="json"),
            "maximums": policy.maximums.model_dump(mode="json"),
            "minimum_evidence": policy.minimum_evidence.model_dump(mode="json"),
            "critical_validator_types": [item.value for item in policy.critical_validator_types],
        },
    }
    (output_dir / "baseline-manifest.json").write_text(
        json.dumps(sanitized_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "validated-quality.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "per-task-classifications.json").write_text(
        json.dumps(per_task, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "quality-policy-outcome.json").write_text(
        json.dumps(policy_outcome, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return outcome


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-model")
    verify.add_argument("--path", type=Path, required=True)
    verify.add_argument("--size", type=int, required=True)
    verify.add_argument("--sha256", required=True)
    sanitize = subparsers.add_parser("sanitize")
    sanitize.add_argument("--baseline-dir", type=Path, required=True)
    sanitize.add_argument("--policy", type=Path, required=True)
    sanitize.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "verify-model":
        print(json.dumps(verify_model(args.path, args.size, args.sha256), sort_keys=True))
        return 0
    outcome = sanitize_baseline(args.baseline_dir, args.policy, args.output_dir)
    print(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
