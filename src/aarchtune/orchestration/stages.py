"""Thin adapters over existing validated stage APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from aarchtune.baseline.models import (
    BaselineManifest,
    BaselineRunConfig,
    BaselineSummary,
    RunStatus,
)
from aarchtune.baseline.runner import run_baseline
from aarchtune.evaluation.models import EvaluationConfig
from aarchtune.evaluation.runner import run_evaluation
from aarchtune.evaluation.validation import validate_evaluation_directory
from aarchtune.finalization.models import FinalizeConfig
from aarchtune.finalization.runner import finalize_evaluation
from aarchtune.finalization.validation import validate_bundle
from aarchtune.optimization.artifacts import validate_plan_directory
from aarchtune.optimization.planner import create_search_plan
from aarchtune.orchestration.errors import StageError
from aarchtune.orchestration.models import OptimizeConfig
from aarchtune.screening.models import ScreeningConfig
from aarchtune.screening.runner import run_screening
from aarchtune.screening.validation import validate_screening_directory


def validate_baseline(path: Path) -> tuple[bool, str | None]:
    required = {
        "manifest.json",
        "hardware.json",
        "runtime-inspection.json",
        "server-command.json",
        "model.json",
        "workload.json",
        "raw-attempts.jsonl",
        "quality-summary.json",
        "baseline-summary.json",
    }
    missing = sorted(name for name in required if not (path / name).is_file())
    if missing:
        return False, f"Missing baseline artifact: {', '.join(missing)}"
    try:
        manifest = BaselineManifest.model_validate_json((path / "manifest.json").read_text())
        summary = BaselineSummary.model_validate_json((path / "baseline-summary.json").read_text())
    except (OSError, ValueError) as exc:
        return False, f"Invalid baseline schema: {exc}"
    if manifest.status is not RunStatus.COMPLETED or summary.status is not RunStatus.COMPLETED:
        return False, "Baseline is not completed"
    if manifest.server_stopped is not True or manifest.sampler_stopped is not True:
        return False, "Baseline cleanup is not confirmed"
    expected = summary.benchmark.measured_attempts_completed
    actual = len([line for line in (path / "raw-attempts.jsonl").read_text().splitlines() if line])
    if actual != expected:
        return False, "Baseline raw-attempt count differs from summary"
    return True, None


def run_baseline_stage(config: OptimizeConfig, path: Path) -> None:
    result = run_baseline(
        BaselineRunConfig(
            binary_path=config.server_binary,
            model_path=config.model,
            workload_path=config.workload,
            output_dir=path,
            repetitions=config.baseline_repetitions,
            warmup_requests=config.warmup_requests,
            request_timeout_seconds=config.request_timeout_seconds,
            startup_timeout_seconds=config.startup_timeout_seconds,
            sample_interval_seconds=config.sample_interval_seconds,
            overwrite=path.exists() and any(path.iterdir()),
        )
    )
    if result.exit_code:
        manifest_path = path / "manifest.json"
        detail = "Baseline execution failed"
        if manifest_path.is_file():
            manifest = BaselineManifest.model_validate_json(manifest_path.read_text())
            if manifest.error_message:
                detail = f"Baseline execution failed: {manifest.error_message}"
        raise StageError(detail)
    valid, reason = validate_baseline(path)
    if not valid:
        raise StageError(reason or "Baseline stage failed")


def run_planning_stage(config: OptimizeConfig, path: Path, baseline: Path) -> None:
    create_search_plan(
        goal=config.goal,
        output_dir=path,
        baseline_dir=baseline,
        search_space_path=config.search_space,
        maximum_profiles=config.max_profiles,
        allow_synthetic=config.allow_synthetic,
        allow_runtime_change=config.allow_runtime_change,
        overwrite=path.exists() and any(path.iterdir()),
    )
    validation = validate_plan_directory(path)
    if not validation.valid:
        raise StageError("Plan validation failed: " + "; ".join(validation.errors))


def run_screening_stage(config: OptimizeConfig, path: Path, plan: Path) -> None:
    result = run_screening(
        ScreeningConfig(
            plan_dir=plan,
            bench_binary=config.bench_binary,
            output_dir=path,
            scenario_path=config.screening_scenarios,
            advance_count=config.advance_count,
            repetitions=config.screening_repetitions,
            total_timeout_seconds=config.maximum_total_duration_seconds,
            sample_interval_seconds=config.sample_interval_seconds,
            allow_synthetic=config.allow_synthetic,
            overwrite=path.exists() and any(path.iterdir()),
        )
    )
    validation = validate_screening_directory(path)
    if result.summary is None:
        raise StageError(f"Screening execution failed with exit code {result.exit_code}")
    if not validation.valid:
        raise StageError("Screening validation failed: " + "; ".join(validation.errors))


def run_evaluation_stage(
    config: OptimizeConfig, path: Path, screening: Path
) -> Literal[0, 2, 3, 4]:
    result = run_evaluation(
        EvaluationConfig(
            screening_dir=screening,
            output_dir=path,
            repetitions=config.evaluation_repetitions,
            warmup_requests=config.warmup_requests,
            quality_policy_path=config.quality_policy,
            request_timeout_seconds=config.request_timeout_seconds,
            startup_timeout_seconds=config.startup_timeout_seconds,
            sample_interval_seconds=config.sample_interval_seconds,
            maximum_total_duration_seconds=config.maximum_total_duration_seconds,
            allow_synthetic=config.allow_synthetic,
            allow_runtime_change=config.allow_runtime_change,
            overwrite=path.exists() and any(path.iterdir()),
        )
    )
    validation = validate_evaluation_directory(path)
    if result.exit_code == 2:
        raise StageError("Evaluation infrastructure failed before a valid comparison")
    if not validation.valid:
        raise StageError("Evaluation validation failed: " + "; ".join(validation.errors))
    return result.exit_code


def run_finalization_stage(
    config: OptimizeConfig, path: Path, evaluation: Path
) -> Literal[0, 1, 3, 4]:
    result = finalize_evaluation(
        FinalizeConfig(
            evaluation_dir=evaluation,
            output_dir=path,
            allow_synthetic=config.allow_synthetic,
            container_image=config.container_image,
            overwrite=path.exists() and any(path.iterdir()),
        )
    )
    validation = validate_bundle(path)
    if not validation.valid:
        raise StageError("Final bundle validation failed: " + "; ".join(validation.errors))
    return result.exit_code
