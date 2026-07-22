"""Quality-constrained real-workload evaluation orchestration."""

from __future__ import annotations

import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from aarchtune.baseline.artifacts import (
    JsonlArtifactWriter,
    atomic_write_json,
    prepare_run_directory,
)
from aarchtune.evaluation.artifacts import EvaluationManifestManager, generate_evaluation_id
from aarchtune.evaluation.candidate_runner import execute_candidate
from aarchtune.evaluation.comparison import compare_candidate
from aarchtune.evaluation.drift import assess_drift
from aarchtune.evaluation.errors import EvaluationArtifactError, EvaluationError
from aarchtune.evaluation.execution_plan import build_evaluation_plan
from aarchtune.evaluation.models import (
    CandidateExecutionResult,
    CandidateExecutionStatus,
    EvaluationConfig,
    EvaluationRunResult,
    EvaluationStatus,
    EvaluationSummary,
    QualityGateStatus,
    SelectedProfile,
    SelectionOutcome,
)
from aarchtune.evaluation.provenance import load_evaluation_input
from aarchtune.evaluation.quality_gate import apply_quality_gate
from aarchtune.evaluation.quality_policy import load_quality_policy
from aarchtune.evaluation.ranking import rank_candidates
from aarchtune.evaluation.selection import select_profile
from aarchtune.evaluation.validation import validate_evaluation_directory
from aarchtune.optimization.artifacts import atomic_write_yaml


def _persist_execution(result: CandidateExecutionResult) -> None:
    atomic_write_json(result.run_directory / "candidate-summary.json", result)
    if result.performance is not None:
        atomic_write_json(result.run_directory / "performance-summary.json", result.performance)


def _run_profile(
    *,
    profile: object,
    label: str,
    directory: Path,
    config: EvaluationConfig,
    model_path: Path,
    workload_path: Path,
    screening_score: float | None,
) -> CandidateExecutionResult:
    from aarchtune.optimization.models import CandidateProfile

    candidate = CandidateProfile.model_validate(profile)
    result = execute_candidate(
        profile=candidate,
        label=label,
        run_directory=directory,
        config=config,
        model_path=model_path,
        workload_path=workload_path,
        screening_score=screening_score,
    )
    _persist_execution(result)
    return result


def run_evaluation(config: EvaluationConfig) -> EvaluationRunResult:
    source = load_evaluation_input(config)
    policy_source = load_quality_policy(config.quality_policy_path)
    root = prepare_run_directory(config.output_dir, overwrite=config.overwrite)
    created_at = datetime.now(UTC)
    evaluation_id = generate_evaluation_id(created_at)
    manager = EvaluationManifestManager(root, evaluation_id, created_at, config)
    started = time.monotonic()
    results: list[CandidateExecutionResult] = []
    failed_executions = 0
    all_servers_stopped = True
    all_samplers_stopped = True
    try:
        manager.update(
            status=EvaluationStatus.VALIDATING_SCREENING,
            stage=EvaluationStatus.VALIDATING_SCREENING,
            screening_reference=source.screening_reference,
        )
        manager.update(
            status=EvaluationStatus.INSPECTING_ENVIRONMENT,
            stage=EvaluationStatus.INSPECTING_ENVIRONMENT,
            hardware_fingerprint=source.current_input.hardware,
            runtime_fingerprint=source.current_input.runtime,
            model_fingerprint=source.current_input.model,
            workload_fingerprint=source.current_input.workload,
            provenance_warnings=source.warnings,
            runtime_change_override_applied=source.runtime_override,
        )
        plan = build_evaluation_plan(evaluation_id, config, source, policy_source)
        manager.update(
            status=EvaluationStatus.BUILDING_PLAN,
            stage=EvaluationStatus.BUILDING_PLAN,
            execution_plan_hash=plan.plan_hash,
        )
        atomic_write_json(root / "screening-reference.json", source.screening_reference)
        atomic_write_json(root / "hardware-fingerprint.json", source.current_input.hardware)
        atomic_write_json(root / "runtime-fingerprint.json", source.current_input.runtime)
        atomic_write_json(root / "model-fingerprint.json", source.current_input.model)
        atomic_write_json(root / "workload-fingerprint.json", source.current_input.workload)
        atomic_write_json(root / "evaluation-config.json", config)
        atomic_write_json(root / "execution-plan.json", plan)
        atomic_write_json(root / "quality-policy.json", policy_source)
        failures_path = root / "failures.jsonl"
        failures_path.touch()
        candidates_root = root / "candidates"
        candidates_root.mkdir()

        manager.update(
            status=EvaluationStatus.RUNNING_BASELINE_START,
            stage=EvaluationStatus.RUNNING_BASELINE_START,
        )
        baseline_start = _run_profile(
            profile=source.baseline_profile,
            label="baseline-start",
            directory=root / "baseline-start",
            config=config,
            model_path=source.current_input.model.path,
            workload_path=source.current_input.workload.path,
            screening_score=None,
        )
        all_servers_stopped &= baseline_start.server_stopped
        all_samplers_stopped &= baseline_start.sampler_stopped
        if baseline_start.status is CandidateExecutionStatus.INTERRUPTED:
            raise KeyboardInterrupt
        if baseline_start.status is not CandidateExecutionStatus.COMPLETED:
            raise EvaluationError("Fresh baseline-start execution did not complete")

        manager.update(
            status=EvaluationStatus.RUNNING_CANDIDATES,
            stage=EvaluationStatus.RUNNING_CANDIDATES,
        )
        with JsonlArtifactWriter(failures_path) as failures:
            for planned in plan.candidates:
                if time.monotonic() - started > config.maximum_total_duration_seconds:
                    raise EvaluationError("Maximum total evaluation duration was reached")
                result = _run_profile(
                    profile=planned.profile,
                    label=f"candidate-{planned.profile.id}",
                    directory=candidates_root / planned.profile.id,
                    config=config,
                    model_path=source.current_input.model.path,
                    workload_path=source.current_input.workload.path,
                    screening_score=planned.screening_score,
                )
                results.append(result)
                all_servers_stopped &= result.server_stopped
                all_samplers_stopped &= result.sampler_stopped
                if result.status is CandidateExecutionStatus.INTERRUPTED:
                    raise KeyboardInterrupt
                if not result.server_stopped or not result.sampler_stopped:
                    raise EvaluationError(
                        f"Cleanup could not be confirmed for candidate {result.candidate_id}"
                    )
                if result.status is not CandidateExecutionStatus.COMPLETED:
                    failed_executions += 1
                    failures.append(
                        {
                            "schema_version": "1.0",
                            "candidate_id": result.candidate_id,
                            "code": result.failure_type or "candidate_execution_failed",
                            "reason": result.failure_message or "Candidate execution failed",
                        }
                    )
                    if failed_executions >= config.maximum_candidate_failures:
                        raise EvaluationError(
                            "Maximum candidate infrastructure failures was reached"
                        )
                if config.settling_delay_seconds:
                    time.sleep(config.settling_delay_seconds)

        manager.update(
            status=EvaluationStatus.RUNNING_BASELINE_END,
            stage=EvaluationStatus.RUNNING_BASELINE_END,
        )
        baseline_end = _run_profile(
            profile=source.baseline_profile,
            label="baseline-end",
            directory=root / "baseline-end",
            config=config,
            model_path=source.current_input.model.path,
            workload_path=source.current_input.workload.path,
            screening_score=None,
        )
        all_servers_stopped &= baseline_end.server_stopped
        all_samplers_stopped &= baseline_end.sampler_stopped
        if baseline_end.status is CandidateExecutionStatus.INTERRUPTED:
            raise KeyboardInterrupt

        manager.update(
            status=EvaluationStatus.ASSESSING_DRIFT,
            stage=EvaluationStatus.ASSESSING_DRIFT,
        )
        drift = assess_drift(baseline_start, baseline_end, policy_source.policy)
        atomic_write_json(root / "drift-assessment.json", drift)

        manager.update(
            status=EvaluationStatus.APPLYING_QUALITY_POLICY,
            stage=EvaluationStatus.APPLYING_QUALITY_POLICY,
        )
        decisions = [
            apply_quality_gate(
                result,
                baseline_start,
                policy_source.policy,
                config.repetitions,
            )
            for result in results
        ]
        comparisons = [compare_candidate(result, baseline_start) for result in results]
        atomic_write_json(
            root / "baseline-comparison.json",
            {
                "schema_version": "1.0",
                "baseline_start_run_id": baseline_start.run_id,
                "comparisons": [item.model_dump(mode="json") for item in comparisons],
            },
        )
        manager.update(status=EvaluationStatus.RANKING, stage=EvaluationStatus.RANKING)
        rankings = rank_candidates(
            results,
            decisions,
            comparisons,
            plan.goal,
            policy_source.policy,
        )
        manager.update(status=EvaluationStatus.SELECTING, stage=EvaluationStatus.SELECTING)
        selection = select_profile(
            rankings=rankings,
            comparisons=comparisons,
            candidates=[item.profile for item in plan.candidates],
            baseline=source.baseline_profile,
            goal=plan.goal,
            policy=policy_source.policy,
            drift=drift,
            screening_reference=source.screening_reference,
        )
        with JsonlArtifactWriter(root / "candidate-results.jsonl") as writer:
            for result in results:
                writer.append(result)
        with JsonlArtifactWriter(root / "quality-decisions.jsonl") as writer:
            for decision in decisions:
                writer.append(decision)
        with JsonlArtifactWriter(root / "candidate-comparisons.jsonl") as writer:
            for comparison in comparisons:
                writer.append(comparison)
        with JsonlArtifactWriter(root / "ranking.jsonl") as writer:
            for ranking in rankings:
                writer.append(ranking)
        atomic_write_json(root / "selection.json", selection)

        if selection.outcome in {
            SelectionOutcome.CANDIDATE_SELECTED,
            SelectionOutcome.BASELINE_RETAINED,
        }:
            selected_result = next(
                item for item in results if item.candidate_id == selection.selected_candidate_id
            )
            selected_comparison = next(
                item for item in comparisons if item.candidate_id == selection.selected_candidate_id
            )
            if selected_result.performance is None or selected_result.quality is None:
                raise EvaluationArtifactError("Selected profile lacks complete evidence")
            selected_profile = SelectedProfile(
                selection_id=selection.selection_id,
                evaluation_id=evaluation_id,
                candidate_id=selected_result.candidate_id,
                candidate_hash=selected_result.candidate_hash,
                hardware_fingerprint=source.current_input.hardware,
                runtime_binary_hash=source.current_input.runtime.binary_sha256,
                model_hash=source.current_input.model.sha256,
                workload_hash=source.current_input.workload.sha256,
                quality_policy_hash=policy_source.sha256,
                goal=plan.goal,
                runtime_configuration=selected_result.profile.runtime.model_dump(mode="json"),
                performance_summary=selected_result.performance,
                quality_summary=selected_result.quality,
                baseline_comparison=selected_comparison,
                limitations=[
                    "Sequential service rate is not multi-client concurrency throughput.",
                    "Non-streaming requests do not expose client-measured TTFT.",
                    "Model-file page caching and deterministic ordering can introduce bias.",
                ],
                scope_statement=(
                    "This profile is specific to the recorded hardware, runtime binary, model, "
                    "workload, and evaluation settings."
                ),
            )
            atomic_write_yaml(
                root / "selected-profile.yaml", selected_profile.model_dump(mode="json")
            )

        final_status = (
            EvaluationStatus.PARTIAL
            if drift.classification.value == "invalidating" or failed_executions
            else EvaluationStatus.COMPLETED
        )
        summary = EvaluationSummary(
            evaluation_id=evaluation_id,
            status=final_status,
            goal=plan.goal,
            advanced_candidates=len(results),
            candidates_completed=sum(
                item.status is CandidateExecutionStatus.COMPLETED for item in results
            ),
            candidates_failed=sum(
                item.status is not CandidateExecutionStatus.COMPLETED for item in results
            ),
            quality_passed=sum(item.status is QualityGateStatus.PASSED for item in decisions),
            quality_rejected=sum(item.status is QualityGateStatus.FAILED for item in decisions),
            drift=drift.classification,
            selection=selection.outcome,
            selected_candidate_id=selection.selected_candidate_id,
            synthetic_fixture=source.screening_reference.synthetic_fixture,
        )
        atomic_write_json(root / "evaluation-summary.json", summary)
        manager.update(
            status=EvaluationStatus.FINALIZING,
            stage=EvaluationStatus.FINALIZING,
            completed_executions=2
            + sum(item.status is CandidateExecutionStatus.COMPLETED for item in results),
            failed_executions=failed_executions
            + int(baseline_end.status is not CandidateExecutionStatus.COMPLETED),
            quality_decisions=len(decisions),
            ranked_candidates=len(rankings),
            selected_candidate_id=selection.selected_candidate_id,
            owned_processes_stopped=all_servers_stopped,
            samplers_stopped=all_samplers_stopped,
            summary=summary,
        )
        validation = validate_evaluation_directory(root, allow_finalizing=True)
        if not validation.valid:
            raise EvaluationArtifactError("; ".join(validation.errors))
        manager.update(status=final_status, stage=final_status)
        if not validate_evaluation_directory(root).valid:
            raise EvaluationArtifactError("Final evaluation artifact validation failed")
        exit_code: Literal[0, 2, 3, 4]
        if selection.outcome is SelectionOutcome.NO_ELIGIBLE_CANDIDATE:
            exit_code = 4
        elif final_status is EvaluationStatus.PARTIAL:
            exit_code = 3
        else:
            exit_code = 0
        return EvaluationRunResult(
            evaluation_id=evaluation_id,
            output_dir=root,
            status=final_status,
            exit_code=exit_code,
            summary=summary,
            selection=selection,
        )
    except KeyboardInterrupt:
        manager.update(
            status=EvaluationStatus.INTERRUPTED,
            stage=EvaluationStatus.INTERRUPTED,
            owned_processes_stopped=all_servers_stopped,
            samplers_stopped=all_samplers_stopped,
            error_type="KeyboardInterrupt",
            error_message="Evaluation interrupted by user",
        )
        return EvaluationRunResult(
            evaluation_id=evaluation_id,
            output_dir=root,
            status=EvaluationStatus.INTERRUPTED,
            exit_code=3,
        )
    except Exception as exc:
        with suppress(OSError):
            manager.update(
                status=EvaluationStatus.FAILED,
                stage=EvaluationStatus.FAILED,
                completed_executions=len(results),
                failed_executions=failed_executions,
                owned_processes_stopped=all_servers_stopped,
                samplers_stopped=all_samplers_stopped,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        if isinstance(exc, EvaluationArtifactError):
            raise
        return EvaluationRunResult(
            evaluation_id=evaluation_id,
            output_dir=root,
            status=EvaluationStatus.FAILED,
            exit_code=2,
        )
