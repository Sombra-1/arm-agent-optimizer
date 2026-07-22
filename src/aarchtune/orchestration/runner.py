"""Doctor-to-finalization orchestration using existing stage implementations."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from aarchtune.baseline.artifacts import atomic_write_json, prepare_run_directory
from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.finalization.models import BundleManifest
from aarchtune.hardware.detector import detect_hardware
from aarchtune.orchestration.artifacts import OptimizeManifestManager
from aarchtune.orchestration.config import configuration_hash, input_fingerprint
from aarchtune.orchestration.errors import OrchestrationError, ResumeError, StageError
from aarchtune.orchestration.models import (
    OptimizeConfig,
    OptimizeFailure,
    OptimizeManifest,
    OptimizeRunResult,
    OptimizeStage,
    OptimizeStageStatus,
    StageReference,
)
from aarchtune.orchestration.resume import PIPELINE, assess_resume
from aarchtune.orchestration.stages import (
    run_baseline_stage,
    run_evaluation_stage,
    run_finalization_stage,
    run_planning_stage,
    run_screening_stage,
)

PRIMARY = {
    OptimizeStage.DOCTOR: "hardware.json",
    OptimizeStage.BASELINE: "manifest.json",
    OptimizeStage.PLANNING: "search-plan.json",
    OptimizeStage.SCREENING: "screening-manifest.json",
    OptimizeStage.EVALUATION: "evaluation-manifest.json",
    OptimizeStage.FINALIZATION: "bundle-manifest.json",
}
DIRECTORIES = {
    OptimizeStage.DOCTOR: "hardware",
    OptimizeStage.BASELINE: "baseline",
    OptimizeStage.PLANNING: "plan",
    OptimizeStage.SCREENING: "screening",
    OptimizeStage.EVALUATION: "evaluation",
    OptimizeStage.FINALIZATION: "final",
}


def _ensure_inputs(config: OptimizeConfig) -> None:
    for label, path in (
        ("llama-server", config.server_binary),
        ("llama-bench", config.bench_binary),
        ("model", config.model),
        ("workload", config.workload),
    ):
        if not path.expanduser().is_file():
            raise OrchestrationError(f"{label} path does not exist: {path}")


def _replace_reference(manager: OptimizeManifestManager, reference: StageReference) -> None:
    stages = [item for item in manager.manifest.stages if item.stage is not reference.stage]
    stages.append(reference)
    stages.sort(key=lambda item: PIPELINE.index(item.stage))
    manager.update(stages=stages)


def _reference(stage: OptimizeStage, root: Path, *, reused: bool) -> StageReference:
    directory = root / DIRECTORIES[stage]
    primary = directory / PRIMARY[stage]
    identity: str | None = None
    if primary.suffix == ".json":
        try:
            value = json.loads(primary.read_text(encoding="utf-8"))
            for key in ("run_id", "plan_id", "screening_id", "evaluation_id", "bundle_id"):
                if key in value:
                    identity = str(value[key])
                    break
        except (OSError, json.JSONDecodeError):
            pass
    return StageReference(
        stage=stage,
        status=OptimizeStageStatus.COMPLETED,
        path=DIRECTORIES[stage],
        identity=identity,
        manifest_sha256=hash_file_streaming(primary),
        reused=reused,
        validation_passed=True,
    )


def _result_from_existing(
    manager: OptimizeManifestManager, root: Path, reused: list[OptimizeStage]
) -> OptimizeRunResult:
    final = BundleManifest.model_validate_json(
        (root / "final/bundle-manifest.json").read_text(encoding="utf-8")
    )
    status = (
        OptimizeStageStatus.COMPLETED
        if final.selected_profile_id is not None
        else OptimizeStageStatus.PARTIAL
    )
    exit_code: Literal[0, 1, 2, 3, 4] = (
        0
        if final.selected_profile_id is not None
        else (4 if final.selection_outcome == "no_eligible_candidate" else 3)
    )
    return OptimizeRunResult(
        optimize_id=manager.manifest.optimize_id,
        output_dir=root,
        status=status,
        exit_code=exit_code,
        outcome=final.selection_outcome,
        selected_profile_id=final.selected_profile_id,
        final_dir=root / "final",
        resumed=True,
        reused_stages=reused,
    )


def run_optimization(config: OptimizeConfig) -> OptimizeRunResult:
    _ensure_inputs(config)
    resolved = config.model_copy(
        update={
            "server_binary": config.server_binary.expanduser().resolve(),
            "bench_binary": config.bench_binary.expanduser().resolve(),
            "model": config.model.expanduser().resolve(),
            "workload": config.workload.expanduser().resolve(),
            "output_dir": config.output_dir.expanduser().resolve(),
        }
    )
    current_config_hash = configuration_hash(resolved)
    current_inputs = input_fingerprint(resolved)
    root = resolved.output_dir
    reused: list[OptimizeStage] = []
    if resolved.resume:
        manifest_path = root / "optimize-manifest.json"
        config_path = root / "optimize-config.json"
        if not manifest_path.is_file() or not config_path.is_file():
            raise ResumeError("Resume requires optimize-manifest.json and optimize-config.json")
        manager = OptimizeManifestManager.load(manifest_path)
        if manager.manifest.configuration_hash != current_config_hash:
            raise ResumeError("Resolved optimization configuration changed; resume rejected")
        if manager.manifest.input_fingerprint != current_inputs:
            raise ResumeError("Input or hardware provenance changed; resume rejected")
        assessment = assess_resume(root, manager.manifest)
        reused = assessment.reused_stages
        for stage in reused:
            _replace_reference(manager, _reference(stage, root, reused=True))
        manager.update(resume_decisions=manager.manifest.resume_decisions + assessment.decisions)
        if assessment.first_stage_to_run is OptimizeStage.COMPLETED:
            return _result_from_existing(manager, root, reused)
        start_index = PIPELINE.index(assessment.first_stage_to_run)
    else:
        root = prepare_run_directory(root, overwrite=resolved.overwrite)
        now = datetime.now(UTC)
        manager = OptimizeManifestManager(
            root / "optimize-manifest.json",
            OptimizeManifest(
                optimize_id=f"optimize-{uuid.uuid4().hex[:12]}",
                created_at=now,
                updated_at=now,
                status=OptimizeStageStatus.RUNNING,
                active_stage=OptimizeStage.INITIALIZING,
                output_directory=root,
                configuration_hash=current_config_hash,
                input_fingerprint=current_inputs,
            ),
        )
        atomic_write_json(root / "optimize-config.json", resolved)
        start_index = 0
    active = PIPELINE[start_index]
    diagnostic_exit: Literal[0, 1, 2, 3, 4] = 0
    try:
        for stage in PIPELINE[start_index:]:
            active = stage
            manager.update(
                status=OptimizeStageStatus.RUNNING,
                active_stage=stage,
                failure=None,
            )
            path = root / DIRECTORIES[stage]
            if stage is OptimizeStage.DOCTOR:
                path.mkdir(parents=True, exist_ok=True)
                hardware = detect_hardware(model_path=resolved.model)
                if not hardware.is_arm64 and not (
                    resolved.allow_non_arm_development or resolved.allow_synthetic
                ):
                    raise StageError(
                        "Real optimization requires AArch64; use --allow-non-arm-development "
                        "or --allow-synthetic for explicit development evidence"
                    )
                atomic_write_json(path / "hardware.json", hardware)
            elif stage is OptimizeStage.BASELINE:
                run_baseline_stage(resolved, path)
            elif stage is OptimizeStage.PLANNING:
                run_planning_stage(resolved, path, root / "baseline")
            elif stage is OptimizeStage.SCREENING:
                run_screening_stage(resolved, path, root / "plan")
            elif stage is OptimizeStage.EVALUATION:
                evaluation_exit = run_evaluation_stage(resolved, path, root / "screening")
                diagnostic_exit = evaluation_exit
            elif stage is OptimizeStage.FINALIZATION:
                finalization_exit = run_finalization_stage(resolved, path, root / "evaluation")
                diagnostic_exit = finalization_exit
            _replace_reference(manager, _reference(stage, root, reused=False))
        final = BundleManifest.model_validate_json(
            (root / "final/bundle-manifest.json").read_text(encoding="utf-8")
        )
        final_status = (
            OptimizeStageStatus.COMPLETED
            if final.selected_profile_id is not None
            else OptimizeStageStatus.PARTIAL
        )
        manager.update(
            status=final_status,
            active_stage=OptimizeStage.COMPLETED
            if final_status is OptimizeStageStatus.COMPLETED
            else OptimizeStage.PARTIAL,
            owned_processes_stopped=True,
            samplers_stopped=True,
        )
        return OptimizeRunResult(
            optimize_id=manager.manifest.optimize_id,
            output_dir=root,
            status=final_status,
            exit_code=diagnostic_exit,
            outcome=final.selection_outcome,
            selected_profile_id=final.selected_profile_id,
            final_dir=root / "final",
            resumed=resolved.resume,
            reused_stages=reused,
        )
    except KeyboardInterrupt:
        manager.update(
            status=OptimizeStageStatus.INTERRUPTED,
            active_stage=OptimizeStage.INTERRUPTED,
            owned_processes_stopped=True,
            samplers_stopped=True,
            failure=OptimizeFailure(
                stage=active,
                error_type="KeyboardInterrupt",
                message="Optimization interrupted by user",
            ),
        )
        return OptimizeRunResult(
            optimize_id=manager.manifest.optimize_id,
            output_dir=root,
            status=OptimizeStageStatus.INTERRUPTED,
            exit_code=3,
            outcome=None,
            selected_profile_id=None,
            final_dir=None,
            resumed=resolved.resume,
            reused_stages=reused,
        )
    except Exception as exc:
        manager.update(
            status=OptimizeStageStatus.FAILED,
            active_stage=OptimizeStage.FAILED,
            owned_processes_stopped=True,
            samplers_stopped=True,
            failure=OptimizeFailure(
                stage=active,
                error_type=type(exc).__name__,
                message=str(exc),
            ),
        )
        if isinstance(exc, OrchestrationError):
            raise
        raise StageError(str(exc)) from exc
