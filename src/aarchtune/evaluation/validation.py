"""Cross-artifact integrity validation for completed evaluations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from aarchtune.evaluation.models import (
    CandidateComparison,
    CandidateExecutionResult,
    CandidateExecutionStatus,
    CandidateRankingResult,
    DriftAssessment,
    DriftClassification,
    EvaluationConfig,
    EvaluationManifest,
    EvaluationPlan,
    EvaluationStatus,
    EvaluationSummary,
    EvaluationValidationResult,
    QualityDecision,
    QualityGateStatus,
    QualityPolicySource,
    ScreeningEvaluationReference,
    SelectedProfile,
    SelectionDecision,
    SelectionOutcome,
)
from aarchtune.optimization.identity import profile_hash
from aarchtune.screening.validation import validate_screening_directory

ModelT = TypeVar("ModelT", bound=BaseModel)
REQUIRED = (
    "evaluation-manifest.json",
    "evaluation-summary.json",
    "screening-reference.json",
    "hardware-fingerprint.json",
    "runtime-fingerprint.json",
    "model-fingerprint.json",
    "workload-fingerprint.json",
    "evaluation-config.json",
    "execution-plan.json",
    "quality-policy.json",
    "baseline-comparison.json",
    "drift-assessment.json",
    "candidate-results.jsonl",
    "quality-decisions.jsonl",
    "candidate-comparisons.jsonl",
    "ranking.jsonl",
    "selection.json",
    "failures.jsonl",
    "baseline-start",
    "baseline-end",
    "candidates",
)
FORBIDDEN = {
    "run-optimized.sh",
    "docker-compose.optimized.yaml",
    "optimization-passport.json",
    "report.html",
}


def _model(path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    return [model.model_validate_json(line) for line in path.read_text().splitlines() if line]


def validate_evaluation_directory(
    path: Path, *, allow_finalizing: bool = False
) -> EvaluationValidationResult:
    root = path.expanduser().resolve()
    missing_errors = [
        f"Missing required artifact: {name}" for name in REQUIRED if not (root / name).exists()
    ]
    if missing_errors:
        return EvaluationValidationResult(
            valid=False,
            evaluation_id=None,
            errors=missing_errors,
            warnings=[],
        )
    errors = [
        f"Forbidden later-phase artifact exists: {name}"
        for name in sorted(FORBIDDEN)
        if (root / name).exists()
    ]
    try:
        manifest = _model(root / "evaluation-manifest.json", EvaluationManifest)
        summary = _model(root / "evaluation-summary.json", EvaluationSummary)
        config = _model(root / "evaluation-config.json", EvaluationConfig)
        plan = _model(root / "execution-plan.json", EvaluationPlan)
        policy = _model(root / "quality-policy.json", QualityPolicySource)
        reference = _model(root / "screening-reference.json", ScreeningEvaluationReference)
        drift = _model(root / "drift-assessment.json", DriftAssessment)
        selection = _model(root / "selection.json", SelectionDecision)
        results = _jsonl(root / "candidate-results.jsonl", CandidateExecutionResult)
        decisions = _jsonl(root / "quality-decisions.jsonl", QualityDecision)
        comparisons = _jsonl(root / "candidate-comparisons.jsonl", CandidateComparison)
        rankings = _jsonl(root / "ranking.jsonl", CandidateRankingResult)
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        return EvaluationValidationResult(
            valid=False,
            evaluation_id=None,
            errors=[f"Evaluation artifact schema failure: {exc}"],
            warnings=[],
        )
    allowed = {
        EvaluationStatus.COMPLETED,
        EvaluationStatus.PARTIAL,
        EvaluationStatus.FAILED,
        EvaluationStatus.INTERRUPTED,
    }
    if allow_finalizing:
        allowed.add(EvaluationStatus.FINALIZING)
    if manifest.status not in allowed:
        errors.append("Evaluation manifest status is not final")
    if manifest.configuration != config:
        errors.append("evaluation-config.json does not match manifest")
    if manifest.summary != summary:
        errors.append("evaluation-summary.json does not match manifest")
    if manifest.execution_plan_hash != plan.plan_hash:
        errors.append("Execution plan hash does not match manifest")
    if plan.quality_policy_sha256 != policy.sha256:
        errors.append("Execution plan quality-policy hash mismatch")
    if manifest.screening_reference != reference or selection.screening_reference != reference:
        errors.append("Screening references are inconsistent")
    screening_validation = validate_screening_directory(reference.path)
    if not screening_validation.valid:
        errors.append("Referenced screening directory no longer validates")
    screening_manifest = reference.path / "screening-manifest.json"
    if (
        not screening_manifest.is_file()
        or hashlib.sha256(screening_manifest.read_bytes()).hexdigest() != reference.manifest_sha256
    ):
        errors.append("Screening manifest hash does not match reference")
    result_ids = [item.candidate_id for item in results]
    if len(result_ids) != len(set(result_ids)):
        errors.append("Candidate results contain duplicate IDs")
    planned_ids = [item.profile.id for item in plan.candidates]
    if result_ids != planned_ids:
        errors.append("Candidate results do not match deterministic execution-plan order")
    decision_by_id = {item.candidate_id: item for item in decisions}
    comparison_by_id = {item.candidate_id: item for item in comparisons}
    result_by_id = {item.candidate_id: item for item in results}
    if set(decision_by_id) != set(result_ids) or len(decisions) != len(results):
        errors.append("Quality decisions do not cover every candidate exactly once")
    if set(comparison_by_id) != set(result_ids) or len(comparisons) != len(results):
        errors.append("Candidate comparisons do not cover every candidate exactly once")
    if manifest.quality_decisions != len(decisions):
        errors.append("Manifest quality-decision count is inconsistent")
    if manifest.ranked_candidates != len(rankings):
        errors.append("Manifest ranking count is inconsistent")
    ranking_ids = [item.candidate_id for item in rankings]
    if len(ranking_ids) != len(set(ranking_ids)):
        errors.append("Ranking contains duplicate candidates")
    if [item.position for item in rankings] != list(range(1, len(rankings) + 1)):
        errors.append("Ranking positions are not contiguous")
    for ranking in rankings:
        result = result_by_id.get(ranking.candidate_id)
        decision = decision_by_id.get(ranking.candidate_id)
        if (
            result is None
            or result.status is not CandidateExecutionStatus.COMPLETED
            or decision is None
            or decision.status is not QualityGateStatus.PASSED
            or not comparison_by_id[ranking.candidate_id].comparable
        ):
            errors.append(f"Ineligible candidate appears in ranking: {ranking.candidate_id}")
    for result in results:
        if profile_hash(result.profile.runtime) != result.candidate_hash:
            errors.append(f"Candidate profile hash mismatch: {result.candidate_id}")
        directory = root / "candidates" / result.candidate_id
        if directory.resolve() != result.run_directory.resolve() or not directory.is_dir():
            errors.append(f"Candidate directory mismatch: {result.candidate_id}")
            continue
        try:
            persisted = _model(directory / "candidate-summary.json", CandidateExecutionResult)
        except (OSError, ValidationError) as exc:
            errors.append(f"Invalid candidate summary {result.candidate_id}: {exc}")
        else:
            if persisted != result:
                errors.append(f"Candidate summary mismatch: {result.candidate_id}")
        raw_path = directory / "raw-attempts.jsonl"
        if result.performance is not None:
            if not raw_path.is_file():
                errors.append(f"Missing raw attempts: {result.candidate_id}")
            elif (
                len([line for line in raw_path.read_text().splitlines() if line])
                != result.performance.completed_attempts
            ):
                errors.append(f"Candidate attempt count mismatch: {result.candidate_id}")
    for sentinel in ("baseline-start", "baseline-end"):
        directory = root / sentinel
        if not (directory / "candidate-summary.json").is_file():
            errors.append(f"Missing {sentinel} candidate summary")
    if drift.classification is DriftClassification.INVALIDATING and selection.outcome not in {
        SelectionOutcome.INVALIDATED_BY_DRIFT,
        SelectionOutcome.EVALUATION_FAILED,
    }:
        errors.append("Invalidating drift was not respected by selection")
    selected_path = root / "selected-profile.yaml"
    if selection.outcome in {
        SelectionOutcome.CANDIDATE_SELECTED,
        SelectionOutcome.BASELINE_RETAINED,
    }:
        if not selected_path.is_file():
            errors.append("Valid selection lacks selected-profile.yaml")
        else:
            try:
                selected = SelectedProfile.model_validate_json(
                    json.dumps(yaml.safe_load(selected_path.read_text()))
                )
            except (OSError, yaml.YAMLError, ValidationError) as exc:
                errors.append(f"Invalid selected profile: {exc}")
            else:
                if (
                    selected.selection_id != selection.selection_id
                    or selected.candidate_id != selection.selected_candidate_id
                    or selected.candidate_hash != selection.selected_candidate_hash
                ):
                    errors.append("Selected profile does not match selection.json")
                decision = decision_by_id.get(selected.candidate_id)
                if decision is None or decision.status is not QualityGateStatus.PASSED:
                    errors.append("Selected candidate did not pass quality")
    elif selected_path.exists():
        errors.append("selected-profile.yaml exists without a valid selection")
    if manifest.owned_processes_stopped is not True or manifest.samplers_stopped is not True:
        errors.append("Manifest does not confirm process and sampler cleanup")
    temporary = [item.name for item in root.rglob(".*.tmp")]
    if temporary:
        errors.append("Temporary incomplete artifacts remain")
    warnings = (
        ["Synthetic real-workload measurements — not Arm performance evidence"]
        if summary.synthetic_fixture
        else []
    )
    return EvaluationValidationResult(
        valid=not errors,
        evaluation_id=manifest.evaluation_id,
        errors=errors,
        warnings=warnings,
    )
