"""Safe resume assessment based on validated, hash-bound stage evidence."""

from __future__ import annotations

from pathlib import Path

from aarchtune.evaluation.validation import validate_evaluation_directory
from aarchtune.finalization.validation import validate_bundle
from aarchtune.optimization.artifacts import validate_plan_directory
from aarchtune.orchestration.errors import ResumeError
from aarchtune.orchestration.models import (
    OptimizeManifest,
    OptimizeStage,
    OptimizeStageStatus,
    ResumeAssessment,
)
from aarchtune.orchestration.stages import validate_baseline
from aarchtune.screening.validation import validate_screening_directory

PIPELINE = [
    OptimizeStage.DOCTOR,
    OptimizeStage.BASELINE,
    OptimizeStage.PLANNING,
    OptimizeStage.SCREENING,
    OptimizeStage.EVALUATION,
    OptimizeStage.FINALIZATION,
]


def _valid(stage: OptimizeStage, path: Path) -> bool:
    if stage is OptimizeStage.DOCTOR:
        return (path / "hardware.json").is_file()
    if stage is OptimizeStage.BASELINE:
        return validate_baseline(path)[0]
    if stage is OptimizeStage.PLANNING:
        return validate_plan_directory(path).valid
    if stage is OptimizeStage.SCREENING:
        return validate_screening_directory(path).valid
    if stage is OptimizeStage.EVALUATION:
        return validate_evaluation_directory(path).valid
    if stage is OptimizeStage.FINALIZATION:
        return validate_bundle(path).valid
    return False


def assess_resume(root: Path, manifest: OptimizeManifest) -> ResumeAssessment:
    references = {item.stage: item for item in manifest.stages}
    reused: list[OptimizeStage] = []
    decisions: list[str] = []
    for stage in PIPELINE:
        reference = references.get(stage)
        if reference is None:
            decisions.append(f"{stage.value}: absent; stage will run")
            return ResumeAssessment(
                resumable=True,
                first_stage_to_run=stage,
                reused_stages=reused,
                decisions=decisions,
            )
        stage_path = root / reference.path
        if reference.status is OptimizeStageStatus.COMPLETED:
            if not _valid(stage, stage_path):
                raise ResumeError(
                    f"Completed {stage.value} evidence failed validation; refusing tampered resume"
                )
            reused.append(stage)
            decisions.append(f"{stage.value}: completed evidence validated and reused")
            continue
        decisions.append(f"{stage.value}: incomplete evidence will be rerun")
        return ResumeAssessment(
            resumable=True,
            first_stage_to_run=stage,
            reused_stages=reused,
            decisions=decisions,
        )
    return ResumeAssessment(
        resumable=True,
        first_stage_to_run=OptimizeStage.COMPLETED,
        reused_stages=reused,
        decisions=decisions,
    )
