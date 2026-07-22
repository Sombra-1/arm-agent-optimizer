"""Optimization Passport construction and canonical integrity verification."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue, ValidationError

from aarchtune import __version__
from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.evaluation.models import QualityGateStatus
from aarchtune.finalization.context import FinalizationContext
from aarchtune.finalization.models import (
    OptimizationPassport,
    ParetoFrontier,
    PassportVerification,
    StageArtifactHash,
)


def canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _relative(path: Path, bundle: Path) -> str:
    return os.path.relpath(path.resolve(), bundle.resolve())


def collect_stage_hashes(context: FinalizationContext, bundle: Path) -> list[StageArtifactHash]:
    files: list[tuple[str, Path]] = [
        ("baseline", context.root / "baseline-start" / "manifest.json"),
        ("baseline", context.root / "baseline-start" / "baseline-summary.json"),
        ("evaluation", context.root / "evaluation-manifest.json"),
        ("evaluation", context.root / "evaluation-config.json"),
        ("evaluation", context.root / "execution-plan.json"),
        ("evaluation", context.root / "evaluation-summary.json"),
        ("evaluation", context.root / "selection.json"),
        ("evaluation", context.root / "quality-policy.json"),
        ("screening", context.screening_root / "screening-manifest.json"),
        ("screening", context.screening_root / "screening-config.json"),
        ("screening", context.screening_root / "scenarios.json"),
        ("screening", context.screening_root / "screening-summary.json"),
        ("planning", context.search_plan_root / "search-plan.json"),
        ("planning", context.search_plan_root / "search-space.json"),
    ]
    if context.baseline_root is not None:
        files.extend(
            [
                ("baseline", context.baseline_root / "manifest.json"),
                ("baseline", context.baseline_root / "baseline-summary.json"),
            ]
        )
    files = [(stage, path) for stage, path in files if path.is_file()]
    return [
        StageArtifactHash(
            stage=stage,
            path=_relative(path, bundle),
            sha256=hash_file_streaming(path),
        )
        for stage, path in files
    ]


def _envelope_data(path: Path) -> dict[str, JsonValue]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    return cast(dict[str, JsonValue], data) if isinstance(data, dict) else {}


def _fastest_rejected(context: FinalizationContext) -> dict[str, JsonValue] | None:
    decisions = {item.candidate_id: item for item in context.decisions}
    rejected = [
        result
        for result in context.results
        if result.performance is not None
        and result.performance.requests_per_minute is not None
        and decisions[result.candidate_id].status is not QualityGateStatus.PASSED
    ]
    if not rejected:
        return None

    def service_rate(item: object) -> float:
        from aarchtune.evaluation.models import CandidateExecutionResult

        result = CandidateExecutionResult.model_validate(item)
        return (
            result.performance.requests_per_minute
            if result.performance and result.performance.requests_per_minute is not None
            else 0.0
        )

    fastest = max(rejected, key=service_rate)
    assert fastest.performance is not None
    decision = decisions[fastest.candidate_id]
    baseline = next((item for item in context.results if item.profile.baseline), None)
    baseline_rpm = (
        baseline.performance.requests_per_minute
        if baseline and baseline.performance is not None
        else None
    )
    rejected_rpm = fastest.performance.requests_per_minute
    service_improvement = (
        (rejected_rpm - baseline_rpm) / baseline_rpm
        if rejected_rpm is not None and baseline_rpm is not None and baseline_rpm > 0
        else None
    )
    return cast(
        dict[str, JsonValue],
        {
            "candidate_id": fastest.candidate_id,
            "candidate_hash": fastest.candidate_hash,
            "requests_per_minute": fastest.performance.requests_per_minute,
            "service_rate_improvement": service_improvement,
            "baseline_task_success_rate": (
                baseline.quality.aggregate.task_attempt_success_rate
                if baseline and baseline.quality
                else None
            ),
            "task_success_rate": (
                fastest.quality.aggregate.task_attempt_success_rate if fastest.quality else None
            ),
            "baseline_json_validity_rate": (
                baseline.quality.aggregate.json_validity_rate
                if baseline and baseline.quality
                else None
            ),
            "json_validity_rate": (
                fastest.quality.aggregate.json_validity_rate if fastest.quality else None
            ),
            "rejection_reasons": [item.model_dump(mode="json") for item in decision.violations],
        },
    )


def create_passport(
    context: FinalizationContext,
    bundle: Path,
    pareto: ParetoFrontier,
    selected_command: list[str] | None,
) -> OptimizationPassport:
    passport_id = f"passport-{uuid.uuid4().hex[:12]}"
    result_by_id = {item.candidate_id: item for item in context.results}
    decision_by_id = {item.candidate_id: item for item in context.decisions}
    comparison_by_id = {item.candidate_id: item for item in context.comparisons}
    selected_id = context.selection.selected_candidate_id
    selected_result = result_by_id.get(selected_id) if selected_id else None
    unavailable = ["time_to_first_token"]
    if selected_result and selected_result.performance:
        for name in (
            "requests_per_minute",
            "p95_latency_seconds",
            "measured_peak_rss_bytes",
            "server_generation_throughput",
        ):
            if getattr(selected_result.performance, name) is None:
                unavailable.append(name)
    bench = context.screening_manifest.llama_bench_fingerprint
    hardware = context.manifest.hardware_fingerprint
    runtime = context.manifest.runtime_fingerprint
    model = context.manifest.model_fingerprint
    workload = context.manifest.workload_fingerprint
    if hardware is None or runtime is None or model is None or workload is None or bench is None:
        raise ValueError("Evaluation or screening provenance is incomplete")
    detailed_hardware = _envelope_data(context.root / "baseline-start" / "hardware.json")
    detailed_runtime = _envelope_data(context.root / "baseline-start" / "runtime-inspection.json")
    detailed_hardware["fingerprint"] = cast(JsonValue, hardware.model_dump(mode="json"))
    detailed_runtime["fingerprint"] = cast(JsonValue, runtime.model_dump(mode="json"))
    baseline_summary: dict[str, JsonValue] = {}
    if (
        context.baseline_root is not None
        and (context.baseline_root / "baseline-summary.json").is_file()
    ):
        baseline_summary = cast(
            dict[str, JsonValue],
            json.loads((context.baseline_root / "baseline-summary.json").read_text()),
        )
    passport = OptimizationPassport(
        passport_id=passport_id,
        generated_at=datetime.now(UTC),
        project_version=__version__,
        outcome=context.selection.outcome.value,
        goal=context.summary.goal.value,
        hardware=detailed_hardware,
        runtime=detailed_runtime,
        llama_bench=cast(dict[str, JsonValue], bench.model_dump(mode="json")),
        model=cast(dict[str, JsonValue], model.model_dump(mode="json")),
        workload=cast(dict[str, JsonValue], workload.model_dump(mode="json")),
        search_space_hash=context.search_plan.search_space.sha256,
        screening_scenario_hash=canonical_hash(
            {
                "scenarios": [
                    item.model_dump(mode="json") for item in context.screening_manifest.scenarios
                ]
            }
        ),
        quality_policy_hash=context.policy.sha256,
        baseline_summary=baseline_summary,
        screening_summary=cast(
            dict[str, JsonValue], context.screening_summary.model_dump(mode="json")
        ),
        evaluation_summary=cast(dict[str, JsonValue], context.summary.model_dump(mode="json")),
        drift_assessment=cast(dict[str, JsonValue], context.drift.model_dump(mode="json")),
        selected_profile=(
            cast(dict[str, JsonValue], context.selected_profile.model_dump(mode="json"))
            if context.selected_profile
            else None
        ),
        selected_command=selected_command,
        quality_decision=(
            cast(dict[str, JsonValue], decision_by_id[selected_id].model_dump(mode="json"))
            if selected_id and selected_id in decision_by_id
            else None
        ),
        performance_comparison=(
            cast(dict[str, JsonValue], comparison_by_id[selected_id].model_dump(mode="json"))
            if selected_id and selected_id in comparison_by_id
            else None
        ),
        fastest_rejected_candidate=_fastest_rejected(context),
        pareto_frontier_reference="pareto-frontier.json",
        stage_artifact_hashes=collect_stage_hashes(context, bundle),
        limitations=[
            "Results are specific to the recorded hardware, model, workload, binaries, and policy.",
            "Sequential requests per minute is not concurrent-client throughput.",
            "Non-streaming evaluation does not measure client-side time to first token.",
            "The baseline-end sentinel reduces but cannot eliminate temporal and thermal bias.",
            *(
                ["Synthetic test evidence is not Arm or model-performance evidence."]
                if context.summary.synthetic_fixture
                else []
            ),
        ],
        reproduction_instructions=[
            "Verify this Passport before using the bundle.",
            "Run reproduce-evaluation.sh to repeat the selected evaluation inputs.",
            "Review report.html and upstream raw evidence before deployment.",
        ],
        synthetic=context.summary.synthetic_fixture,
        hardware_specific_disclaimer=(
            "This selection is specific to the recorded hardware, runtime binary, model, "
            "workload, generation settings, and quality policy."
        ),
        selection_explanation=context.selection.reason,
        unavailable_metrics=sorted(set(unavailable)),
        passport_content_hash="pending",
    )
    content = passport.model_dump(mode="json")
    content.pop("passport_content_hash")
    return passport.model_copy(update={"passport_content_hash": canonical_hash(content)})


def verify_passport(path: Path) -> PassportVerification:
    errors: list[str] = []
    try:
        passport = OptimizationPassport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        return PassportVerification(
            valid=False,
            passport_id=None,
            content_hash_valid=False,
            errors=[f"Invalid Passport: {exc}"],
        )
    content = passport.model_dump(mode="json")
    recorded = content.pop("passport_content_hash")
    hash_valid = canonical_hash(content) == recorded
    if not hash_valid:
        errors.append("Passport canonical content hash does not match")
    if "specific to the recorded hardware" not in passport.hardware_specific_disclaimer:
        errors.append("Hardware-specific disclaimer is missing")
    if not passport.stage_artifact_hashes:
        errors.append("Stage provenance references are missing")
    for reference in passport.stage_artifact_hashes:
        artifact = (path.parent / reference.path).resolve()
        if not artifact.is_file() or hash_file_streaming(artifact) != reference.sha256:
            errors.append(f"Stage artifact hash mismatch: {reference.path}")
    selection_reference = next(
        (item for item in passport.stage_artifact_hashes if item.path.endswith("selection.json")),
        None,
    )
    if selection_reference is None:
        errors.append("Evaluation selection reference is missing")
    else:
        selection_path = (path.parent / selection_reference.path).resolve()
        if selection_path.is_file():
            try:
                selection = json.loads(selection_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                errors.append("Evaluation selection reference is not valid JSON")
            else:
                selected = passport.selected_profile or {}
                if selection.get("outcome") != passport.outcome:
                    errors.append("Passport outcome differs from evaluation selection")
                if selection.get("selected_candidate_id") != selected.get("candidate_id"):
                    errors.append("Passport selected profile differs from evaluation selection")
    summary_reference = next(
        (
            item
            for item in passport.stage_artifact_hashes
            if item.path.endswith("evaluation-summary.json")
        ),
        None,
    )
    if summary_reference is not None:
        summary_path = (path.parent / summary_reference.path).resolve()
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                errors.append("Evaluation summary reference is not valid JSON")
            else:
                if summary.get("synthetic_fixture") != passport.synthetic:
                    errors.append("Passport synthetic status differs from evaluation evidence")
    runtime_fingerprint = passport.runtime.get("fingerprint")
    runtime_hashes = runtime_fingerprint if isinstance(runtime_fingerprint, dict) else {}
    required_hashes = (
        (runtime_hashes, "binary_sha256", "runtime binary"),
        (passport.model, "sha256", "model"),
        (passport.workload, "sha256", "workload"),
    )
    for provenance, field, label in required_hashes:
        if not isinstance(provenance.get(field), str) or len(str(provenance[field])) != 64:
            errors.append(f"Required {label} hash is missing")
    if passport.synthetic and not any("synthetic" in item.lower() for item in passport.limitations):
        # Synthetic status is still explicit in the schema; add a warning-grade integrity guard.
        errors.append("Synthetic Passport lacks a synthetic limitation statement")
    return PassportVerification(
        valid=not errors,
        passport_id=passport.passport_id,
        content_hash_valid=hash_valid,
        errors=errors,
    )
