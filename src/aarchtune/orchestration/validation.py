"""Validate one-command stage references and native artifact integrity."""

from __future__ import annotations

from pathlib import Path

from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.evaluation.validation import validate_evaluation_directory
from aarchtune.finalization.validation import validate_bundle
from aarchtune.optimization.artifacts import validate_plan_directory
from aarchtune.orchestration.models import (
    OptimizeManifest,
    OptimizeStage,
    OptimizeStageStatus,
    OptimizeValidationResult,
)
from aarchtune.orchestration.resume import PIPELINE
from aarchtune.orchestration.runner import DIRECTORIES, PRIMARY
from aarchtune.orchestration.stages import validate_baseline
from aarchtune.screening.validation import validate_screening_directory


def validate_optimization(path: Path) -> OptimizeValidationResult:
    root = path.expanduser().resolve()
    errors: list[str] = []
    try:
        manifest = OptimizeManifest.model_validate_json(
            (root / "optimize-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        return OptimizeValidationResult(
            valid=False, optimize_id=None, errors=[f"Invalid optimize manifest: {exc}"], warnings=[]
        )
    if manifest.status not in {OptimizeStageStatus.COMPLETED, OptimizeStageStatus.PARTIAL}:
        errors.append("Optimize manifest is not in a valid terminal state")
    references = {item.stage: item for item in manifest.stages}
    for stage in PIPELINE:
        reference = references.get(stage)
        if reference is None or reference.status is not OptimizeStageStatus.COMPLETED:
            errors.append(f"Required stage reference is absent or incomplete: {stage.value}")
            continue
        stage_path = root / reference.path
        primary = stage_path / PRIMARY[stage]
        if not primary.is_file() or hash_file_streaming(primary) != reference.manifest_sha256:
            errors.append(f"Stage primary artifact hash mismatch: {stage.value}")
            continue
        valid = True
        if stage is OptimizeStage.BASELINE:
            valid = validate_baseline(stage_path)[0]
        elif stage is OptimizeStage.PLANNING:
            valid = validate_plan_directory(stage_path).valid
        elif stage is OptimizeStage.SCREENING:
            valid = validate_screening_directory(stage_path).valid
        elif stage is OptimizeStage.EVALUATION:
            valid = validate_evaluation_directory(stage_path).valid
        elif stage is OptimizeStage.FINALIZATION:
            valid = validate_bundle(stage_path).valid
        if not valid:
            errors.append(f"Native stage validation failed: {stage.value}")
    if manifest.owned_processes_stopped is not True or manifest.samplers_stopped is not True:
        errors.append("Optimize manifest does not confirm process and sampler cleanup")
    if any(root.rglob(".*.tmp")):
        errors.append("Temporary incomplete artifacts remain")
    synthetic = False
    final_manifest = root / DIRECTORIES[OptimizeStage.FINALIZATION] / "bundle-manifest.json"
    if final_manifest.is_file():
        import json

        synthetic = bool(json.loads(final_manifest.read_text()).get("synthetic"))
    return OptimizeValidationResult(
        valid=not errors,
        optimize_id=manifest.optimize_id,
        errors=errors,
        warnings=(["Synthetic test evidence — not Arm performance evidence"] if synthetic else []),
    )
