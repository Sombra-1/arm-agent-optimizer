"""Offline screening artifact integrity and provenance validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.optimization.artifacts import validate_plan_directory
from aarchtune.optimization.identity import profile_hash, stable_hash
from aarchtune.optimization.models import CandidateProfile, SearchPlan
from aarchtune.screening.models import (
    BenchExecutionResult,
    BenchSignature,
    CandidateAdvancementDecision,
    DecisionStatus,
    LlamaBenchCapabilities,
    MatrixEntry,
    NormalizedBenchMeasurement,
    ScenarioSource,
    ScreeningConfig,
    ScreeningManifest,
    ScreeningStatus,
    ScreeningSummary,
    ScreeningValidationResult,
    SearchPlanReference,
    SignatureMembership,
    SignatureScreeningResult,
)

REQUIRED_ARTIFACTS = (
    "screening-manifest.json",
    "screening-summary.json",
    "search-plan-reference.json",
    "hardware-fingerprint.json",
    "model-fingerprint.json",
    "llama-bench-inspection.json",
    "screening-config.json",
    "scenarios.json",
    "bench-signatures.jsonl",
    "signature-membership.jsonl",
    "benchmark-matrix.jsonl",
    "raw-executions.jsonl",
    "normalized-measurements.jsonl",
    "signature-results.jsonl",
    "advancement-decisions.jsonl",
    "advanced-candidates.jsonl",
    "non-advanced-candidates.jsonl",
    "failures.jsonl",
    "process-summaries.jsonl",
    "logs",
    "advanced-profiles",
)
FORBIDDEN_ARTIFACTS = {
    "best-profile.yaml",
    "run-optimized.sh",
    "optimization-passport.json",
    "report.html",
}
ModelT = TypeVar("ModelT", bound=BaseModel)


def _json(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not an object")
    return value


def _model(path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    values = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                values.append(model.model_validate_json(line))
            except ValidationError as exc:
                raise ValueError(f"{path.name} line {number}: {exc}") from exc
    return values


def validate_screening_directory(
    path: Path, *, allow_finalizing: bool = False
) -> ScreeningValidationResult:
    root = path.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    missing = [name for name in REQUIRED_ARTIFACTS if not (root / name).exists()]
    if missing:
        return ScreeningValidationResult(
            valid=False,
            screening_id=None,
            errors=[f"Missing required artifact: {name}" for name in missing],
            warnings=[],
        )
    errors.extend(
        f"Forbidden final-optimization artifact exists: {name}"
        for name in sorted(FORBIDDEN_ARTIFACTS)
        if (root / name).exists()
    )
    try:
        manifest = _model(root / "screening-manifest.json", ScreeningManifest)
        config = _model(root / "screening-config.json", ScreeningConfig)
        reference = _model(root / "search-plan-reference.json", SearchPlanReference)
        bench = _model(root / "llama-bench-inspection.json", LlamaBenchCapabilities)
        scenario_source = _model(root / "scenarios.json", ScenarioSource)
        summary = _model(root / "screening-summary.json", ScreeningSummary)
        signatures = _jsonl(root / "bench-signatures.jsonl", BenchSignature)
        memberships = _jsonl(root / "signature-membership.jsonl", SignatureMembership)
        matrix = _jsonl(root / "benchmark-matrix.jsonl", MatrixEntry)
        executions = _jsonl(root / "raw-executions.jsonl", BenchExecutionResult)
        measurements = _jsonl(root / "normalized-measurements.jsonl", NormalizedBenchMeasurement)
        signature_results = _jsonl(root / "signature-results.jsonl", SignatureScreeningResult)
        decisions = _jsonl(root / "advancement-decisions.jsonl", CandidateAdvancementDecision)
        advanced = _jsonl(root / "advanced-candidates.jsonl", CandidateProfile)
        non_advanced = _jsonl(root / "non-advanced-candidates.jsonl", CandidateProfile)
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        return ScreeningValidationResult(
            valid=False,
            screening_id=None,
            errors=[f"Screening artifact schema failure: {exc}", *errors],
            warnings=[],
        )
    allowed_statuses = {
        ScreeningStatus.COMPLETED,
        ScreeningStatus.PARTIAL,
        ScreeningStatus.FAILED,
        ScreeningStatus.INTERRUPTED,
    }
    if allow_finalizing:
        allowed_statuses.add(ScreeningStatus.FINALIZING)
    if manifest.status not in allowed_statuses:
        errors.append(f"Manifest status is not final: {manifest.status.value}")
    if manifest.screening_configuration.model_dump(mode="json") != config.model_dump(mode="json"):
        errors.append("screening-config.json does not match the manifest")
    if manifest.search_plan_reference != reference:
        errors.append("search-plan-reference.json does not match the manifest")
    if manifest.hardware_fingerprint != _json(root / "hardware-fingerprint.json"):
        errors.append("hardware-fingerprint.json does not match the manifest")
    if manifest.model_fingerprint != _json(root / "model-fingerprint.json"):
        errors.append("model-fingerprint.json does not match the manifest")
    if manifest.scenarios != scenario_source.scenarios:
        errors.append("scenarios.json does not match the manifest")
    if manifest.summary != summary:
        errors.append("screening-summary.json does not match the manifest")
    fingerprint = manifest.llama_bench_fingerprint
    if fingerprint is None or (
        fingerprint.path != bench.binary_path
        or fingerprint.sha256 != bench.binary_sha256
        or fingerprint.size_bytes != bench.binary_size
        or fingerprint.modification_time_ns != bench.binary_mtime_ns
        or fingerprint.version != bench.version
        or fingerprint.synthetic_fixture != bench.synthetic_fixture
    ):
        errors.append("llama-bench-inspection.json does not match the manifest fingerprint")
    plan_result = validate_plan_directory(reference.path)
    if not plan_result.valid:
        errors.append("Referenced search plan no longer validates")
        plan = None
    else:
        try:
            plan = SearchPlan.model_validate_json(
                (reference.path / "search-plan.json").read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            errors.append(f"Cannot load referenced search plan: {exc}")
            plan = None
    if plan is not None:
        if plan.plan_hash != reference.plan_hash or plan.plan_id != reference.plan_id:
            errors.append("Search-plan hash or ID does not match screening reference")
        plan_candidates = {candidate.id: candidate for candidate in plan.candidates}
        if len(decisions) != len(plan_candidates):
            errors.append("Every planned candidate must have exactly one advancement decision")
        if len({decision.candidate_id for decision in decisions}) != len(decisions):
            errors.append("Advancement decisions contain duplicate candidate IDs")
        for decision in decisions:
            candidate = plan_candidates.get(decision.candidate_id)
            if candidate is None or candidate.profile_hash != decision.candidate_hash:
                errors.append(f"Candidate hash mismatch in decision: {decision.candidate_id}")
        if len({membership.candidate_id for membership in memberships}) != len(memberships):
            errors.append("Signature memberships contain duplicate candidate IDs")
        for membership in memberships:
            candidate = plan_candidates.get(membership.candidate_id)
            if candidate is None or candidate.profile_hash != membership.candidate_hash:
                errors.append(
                    f"Candidate hash mismatch in signature membership: {membership.candidate_id}"
                )
        if {membership.candidate_id for membership in memberships} != set(plan_candidates):
            errors.append("Signature memberships do not cover every planned candidate")
    if (
        not bench.binary_path.is_file()
        or hash_file_streaming(bench.binary_path) != bench.binary_sha256
    ):
        errors.append("llama-bench binary hash no longer matches screening provenance")
    model_data = _json(root / "model-fingerprint.json")
    model_path = Path(str(model_data.get("path", "")))
    if not model_path.is_file() or hash_file_streaming(model_path) != model_data.get("sha256"):
        errors.append("Model hash no longer matches screening provenance")
    signature_by_id = {signature.id: signature for signature in signatures}
    if len(signature_by_id) != len(signatures):
        errors.append("Duplicate benchmark signature IDs")
    for signature in signatures:
        if stable_hash(signature.settings) != signature.signature_hash:
            errors.append(f"Benchmark signature hash mismatch: {signature.id}")
    for membership in memberships:
        found_signature = signature_by_id.get(membership.bench_signature_id)
        if (
            found_signature is None
            or found_signature.signature_hash != membership.bench_signature_hash
        ):
            errors.append(f"Signature membership mismatch: {membership.candidate_id}")
    scenario_ids = {scenario.id for scenario in scenario_source.scenarios}
    matrix_ids = {entry.invocation_id for entry in matrix}
    if len(matrix_ids) != len(matrix):
        errors.append("Duplicate benchmark matrix invocation IDs")
    for entry in matrix:
        matrix_signature = signature_by_id.get(entry.signature_id)
        if matrix_signature is None or matrix_signature.signature_hash != entry.signature_hash:
            errors.append(f"Benchmark matrix signature mismatch: {entry.invocation_id}")
        if entry.scenario_id not in scenario_ids:
            errors.append(f"Benchmark matrix scenario mismatch: {entry.invocation_id}")
    execution_ids = {execution.invocation_id for execution in executions}
    if len(execution_ids) != len(executions):
        errors.append("Duplicate raw execution IDs")
    for execution in executions:
        if execution.invocation_id not in matrix_ids:
            errors.append(f"Raw execution is absent from matrix: {execution.invocation_id}")
        for artifact in (
            execution.stdout_path,
            execution.stderr_path,
            execution.process_samples_path,
        ):
            if not Path(artifact).is_file():
                errors.append(
                    f"Raw execution {execution.invocation_id} references "
                    f"missing artifact: {artifact}"
                )
    for measurement in measurements:
        if measurement.invocation_id not in execution_ids:
            errors.append(
                f"Normalized measurement lacks raw execution: {measurement.measurement_id}"
            )
    result_ids = {result.signature_id for result in signature_results}
    if len(result_ids) != len(signature_results) or result_ids != set(signature_by_id):
        errors.append("Signature results do not match benchmark signatures")
    advanced_ids = [candidate.id for candidate in advanced]
    if len(advanced_ids) != len(set(advanced_ids)):
        errors.append("Duplicate advanced candidates")
    if len(advanced) > config.advance_count:
        errors.append("Advanced candidate count exceeds configured limit")
    advanced_and_other = [*advanced, *non_advanced]
    all_candidate_ids = [candidate.id for candidate in advanced_and_other]
    if len(all_candidate_ids) != len(set(all_candidate_ids)):
        errors.append("Advanced and non-advanced candidate sets overlap or contain duplicates")
    if plan is not None and set(all_candidate_ids) != {
        candidate.id for candidate in plan.candidates
    }:
        errors.append("Advanced and non-advanced artifacts do not cover the search plan")
    for candidate in advanced_and_other:
        if profile_hash(candidate.runtime) != candidate.profile_hash:
            errors.append(f"Persisted candidate profile hash mismatch: {candidate.id}")
    decision_by_id = {decision.candidate_id: decision for decision in decisions}
    for candidate in advanced:
        candidate_decision = decision_by_id.get(candidate.id)
        if candidate_decision is None or candidate_decision.decision is not DecisionStatus.ADVANCED:
            errors.append(f"Advanced candidate has no matching decision: {candidate.id}")
        if not candidate.executable or not candidate.compatibility.compatible:
            errors.append(f"Incompatible candidate was advanced: {candidate.id}")
        yaml_path = root / "advanced-profiles" / f"{candidate.id}.yaml"
        try:
            loaded = CandidateProfile.model_validate_json(
                json.dumps(yaml.safe_load(yaml_path.read_text()))
            )
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"Invalid advanced profile YAML {candidate.id}: {exc}")
        else:
            if loaded.model_dump(mode="json") != candidate.model_dump(mode="json"):
                errors.append(f"Advanced profile YAML mismatch: {candidate.id}")
    expected_yaml = {f"{candidate.id}.yaml" for candidate in advanced}
    actual_yaml = {item.name for item in (root / "advanced-profiles").glob("*.yaml")}
    if expected_yaml != actual_yaml:
        errors.append("Advanced profile YAML file set does not match advanced candidates")
    if manifest.completed_invocations != len(executions):
        errors.append("Manifest completed invocation count is inconsistent")
    if manifest.normalized_results != len(measurements):
        errors.append("Manifest normalized result count is inconsistent")
    if manifest.advancement_decisions != len(decisions):
        errors.append("Manifest advancement decision count is inconsistent")
    if manifest.advanced_candidate_count != len(advanced):
        errors.append("Manifest advanced candidate count is inconsistent")
    if manifest.summary is not None and manifest.summary.synthetic_fixture:
        warnings.append("Synthetic low-level measurements — not Arm performance evidence")
    return ScreeningValidationResult(
        valid=not errors,
        screening_id=manifest.screening_id,
        errors=errors,
        warnings=warnings,
    )
