"""Bounded sequential llama-bench screening orchestration."""

from __future__ import annotations

import subprocess
import time
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from aarchtune.baseline.artifacts import (
    JsonlArtifactWriter,
    atomic_write_json,
    prepare_run_directory,
)
from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.hardware.detector import detect_hardware
from aarchtune.optimization.artifacts import atomic_write_yaml, validate_plan_directory
from aarchtune.optimization.compatibility import hardware_fingerprint, runtime_fingerprint
from aarchtune.optimization.models import SearchPlan
from aarchtune.runtime.capabilities import inspect_llama_server_capabilities
from aarchtune.runtime.one_shot import run_one_shot
from aarchtune.screening.artifacts import ScreeningManifestManager, screening_id
from aarchtune.screening.capabilities import inspect_llama_bench
from aarchtune.screening.command import build_bench_command
from aarchtune.screening.errors import BenchParseError, ScreeningArtifactError, ScreeningError
from aarchtune.screening.models import (
    BenchExecutionResult,
    LlamaBenchFingerprint,
    MatrixEntry,
    NormalizedBenchMeasurement,
    ScreeningConfig,
    ScreeningRunResult,
    ScreeningStatus,
    ScreeningSummary,
    SearchPlanReference,
    SignatureStatus,
)
from aarchtune.screening.normalization import normalize_record
from aarchtune.screening.parser import parse_bench_output
from aarchtune.screening.scenarios import load_scenarios
from aarchtune.screening.selection import score_signatures, select_candidates
from aarchtune.screening.signatures import build_signatures
from aarchtune.screening.stability import aggregate_signature
from aarchtune.screening.validation import validate_screening_directory


def _load_plan(path: Path) -> SearchPlan:
    validation = validate_plan_directory(path)
    if not validation.valid:
        raise ScreeningError(
            "Search plan failed integrity validation: " + "; ".join(validation.errors)
        )
    try:
        return SearchPlan.model_validate_json(
            (path.expanduser().resolve() / "search-plan.json").read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ScreeningError(f"Cannot load validated search plan: {exc}") from exc


def _failure(invocation_id: str, code: str, reason: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "invocation_id": invocation_id,
        "code": code,
        "reason": reason,
    }


def run_screening(config: ScreeningConfig) -> ScreeningRunResult:
    plan_root = config.plan_dir.expanduser().resolve()
    plan = _load_plan(plan_root)
    if plan.summary.synthetic_fixture and not config.allow_synthetic:
        raise ScreeningError("Synthetic search plans require --allow-synthetic")
    model_path = plan.input.model.path.expanduser().resolve()
    if not model_path.is_file():
        raise ScreeningError(f"Planned model path is unavailable: {model_path}")
    if hash_file_streaming(model_path) != plan.input.model.sha256:
        raise ScreeningError("Model hash differs from the validated search plan")
    workload_path = plan.input.workload.path.expanduser().resolve()
    if (
        not workload_path.is_file()
        or hash_file_streaming(workload_path) != plan.input.workload.sha256
    ):
        raise ScreeningError("Workload provenance differs from the validated search plan")
    runtime_binary = plan.input.runtime.binary_path
    if (
        not runtime_binary.is_file()
        or hash_file_streaming(runtime_binary) != plan.input.runtime.binary_sha256
    ):
        raise ScreeningError("Runtime binary provenance differs from the search plan")
    current_hardware_report = detect_hardware(model_path=model_path)
    if current_hardware_report.architecture != plan.input.hardware.architecture:
        raise ScreeningError("Current architecture differs from the search-plan hardware")
    current_hardware = hardware_fingerprint(current_hardware_report)
    if current_hardware.fingerprint_hash != plan.input.hardware.fingerprint_hash:
        raise ScreeningError(
            "Current hardware fingerprint differs from the search plan; regenerate the plan"
        )
    current_runtime = runtime_fingerprint(
        inspect_llama_server_capabilities(runtime_binary, include_probe_output=True)
    )
    if current_runtime.fingerprint_hash != plan.input.runtime.fingerprint_hash:
        raise ScreeningError(
            "Current runtime version or capability fingerprint differs from the search plan"
        )
    root = prepare_run_directory(config.output_dir, overwrite=config.overwrite)
    logs = root / "logs"
    profiles = root / "advanced-profiles"
    logs.mkdir()
    profiles.mkdir()
    identifier = screening_id(plan.plan_hash)
    manager = ScreeningManifestManager(root, identifier, config)
    plan_reference = SearchPlanReference(
        path=plan_root,
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        goal=plan.goal,
        candidate_count=len(plan.candidates),
        synthetic_fixture=plan.summary.synthetic_fixture,
    )
    try:
        manager.update(
            status=ScreeningStatus.VALIDATING_PLAN,
            stage=ScreeningStatus.VALIDATING_PLAN,
            search_plan_reference=plan_reference,
            hardware_fingerprint=current_hardware.model_dump(mode="json"),
            model_fingerprint=plan.input.model.model_dump(mode="json"),
        )
        manager.update(
            status=ScreeningStatus.INSPECTING_BENCH,
            stage=ScreeningStatus.INSPECTING_BENCH,
        )
        capabilities = inspect_llama_bench(
            config.bench_binary,
            plan=plan,
            include_probe_output=True,
        )
        synthetic = plan.summary.synthetic_fixture or capabilities.synthetic_fixture
        if synthetic and not config.allow_synthetic:
            raise ScreeningError("Synthetic screening evidence requires --allow-synthetic")
        bench_fingerprint = LlamaBenchFingerprint(
            path=capabilities.binary_path,
            sha256=capabilities.binary_sha256,
            size_bytes=capabilities.binary_size,
            modification_time_ns=capabilities.binary_mtime_ns,
            version=capabilities.version,
            synthetic_fixture=capabilities.synthetic_fixture,
        )
        scenario_source = load_scenarios(config.scenario_path, capabilities)
        if len(scenario_source.scenarios) > config.maximum_scenarios:
            raise ScreeningError(
                f"Scenario count {len(scenario_source.scenarios)} exceeds configured maximum "
                f"{config.maximum_scenarios}"
            )
        manager.update(
            status=ScreeningStatus.BUILDING_MATRIX,
            stage=ScreeningStatus.BUILDING_MATRIX,
            llama_bench_fingerprint=bench_fingerprint,
            scenarios=scenario_source.scenarios,
        )
        signatures, memberships = build_signatures(plan.candidates, capabilities)
        compatible_signatures = [signature for signature in signatures if signature.compatible]
        if len(compatible_signatures) > config.maximum_unique_signatures:
            raise ScreeningError(
                f"Unique signature count {len(compatible_signatures)} exceeds configured maximum "
                f"{config.maximum_unique_signatures}"
            )
        matrix: list[MatrixEntry] = []
        for signature in compatible_signatures:
            for scenario in scenario_source.scenarios:
                for repetition in range(1, config.repetitions + 1):
                    invocation_id = (
                        f"inv-{signature.signature_hash[:10]}-{scenario.id}-r{repetition}"
                    )
                    matrix.append(
                        MatrixEntry(
                            invocation_id=invocation_id,
                            signature_id=signature.id,
                            signature_hash=signature.signature_hash,
                            scenario_id=scenario.id,
                            repetition=repetition,
                            command=build_bench_command(
                                capabilities,
                                model_path,
                                signature,
                                scenario,
                                repetition,
                            ),
                        )
                    )
        if len(matrix) > config.maximum_invocations:
            raise ScreeningError(
                f"Expected invocation count {len(matrix)} exceeds configured maximum "
                f"{config.maximum_invocations}"
            )
        atomic_write_json(root / "search-plan-reference.json", plan_reference)
        atomic_write_json(root / "hardware-fingerprint.json", current_hardware)
        atomic_write_json(root / "model-fingerprint.json", plan.input.model)
        atomic_write_json(root / "llama-bench-inspection.json", capabilities)
        atomic_write_json(root / "screening-config.json", config)
        atomic_write_json(root / "scenarios.json", scenario_source)
        manager.update(signature_membership=memberships)
        executions: list[BenchExecutionResult] = []
        measurements: list[NormalizedBenchMeasurement] = []
        failure_count = 0
        all_processes_stopped = True
        all_samplers_stopped = True
        started = time.monotonic()
        with ExitStack() as stack:
            signature_writer = stack.enter_context(
                JsonlArtifactWriter(root / "bench-signatures.jsonl")
            )
            membership_writer = stack.enter_context(
                JsonlArtifactWriter(root / "signature-membership.jsonl")
            )
            matrix_writer = stack.enter_context(
                JsonlArtifactWriter(root / "benchmark-matrix.jsonl")
            )
            execution_writer = stack.enter_context(
                JsonlArtifactWriter(root / "raw-executions.jsonl")
            )
            measurement_writer = stack.enter_context(
                JsonlArtifactWriter(root / "normalized-measurements.jsonl")
            )
            process_writer = stack.enter_context(
                JsonlArtifactWriter(root / "process-summaries.jsonl")
            )
            failure_writer = stack.enter_context(JsonlArtifactWriter(root / "failures.jsonl"))
            for signature in signatures:
                signature_writer.append(signature)
            for membership in memberships:
                membership_writer.append(membership)
            for entry in matrix:
                matrix_writer.append(entry)
            manager.update(status=ScreeningStatus.EXECUTING, stage=ScreeningStatus.EXECUTING)
            signature_by_id = {signature.id: signature for signature in signatures}
            scenario_by_id = {scenario.id: scenario for scenario in scenario_source.scenarios}
            for entry in matrix:
                if time.monotonic() - started >= config.total_timeout_seconds:
                    failure_count += 1
                    failure_writer.append(
                        _failure(
                            entry.invocation_id,
                            "total_timeout",
                            "Maximum total screening duration was reached",
                        )
                    )
                    continue
                invocation_dir = logs / entry.invocation_id
                invocation_dir.mkdir()
                stdout_path = invocation_dir / f"stdout.{entry.command.output_format.value}"
                stderr_path = invocation_dir / "stderr.log"
                sample_path = invocation_dir / "process-samples.jsonl"
                try:
                    execution = run_one_shot(
                        entry.command,
                        invocation_id=entry.invocation_id,
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                        samples_path=sample_path,
                        timeout_seconds=config.invocation_timeout_seconds,
                        shutdown_timeout_seconds=config.shutdown_timeout_seconds,
                        sample_interval_seconds=config.sample_interval_seconds,
                        maximum_log_bytes=config.maximum_log_bytes,
                        extra_environment={"AARCHTUNE_SCREEN_REPETITION": str(entry.repetition)},
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    failure_count += 1
                    failure_writer.append(
                        _failure(entry.invocation_id, "process_start_failure", str(exc))
                    )
                    continue
                executions.append(execution)
                execution_writer.append(execution)
                process_writer.append(execution.process_summary)
                all_processes_stopped &= execution.process_stopped
                all_samplers_stopped &= execution.sampler_stopped
                if execution.interrupted:
                    raise KeyboardInterrupt
                if execution.timed_out or execution.exit_code != 0:
                    failure_count += 1
                    code = "timeout" if execution.timed_out else "nonzero_exit"
                    failure_writer.append(
                        _failure(entry.invocation_id, code, f"exit_code={execution.exit_code}")
                    )
                    continue
                try:
                    records = parse_bench_output(
                        stdout_path, entry.command.output_format, entry.invocation_id
                    )
                except BenchParseError as exc:
                    failure_count += 1
                    failure_writer.append(_failure(entry.invocation_id, "parser_failure", str(exc)))
                    continue
                provenance_errors: list[str] = []
                for record in records:
                    normalized = normalize_record(
                        record,
                        signature_by_id[entry.signature_id],
                        scenario_by_id[entry.scenario_id],
                    )
                    measurements.append(normalized)
                    measurement_writer.append(normalized)
                    if not normalized.provenance_valid:
                        provenance_errors.extend(normalized.provenance_errors)
                if provenance_errors:
                    failure_count += 1
                    failure_writer.append(
                        _failure(
                            entry.invocation_id,
                            "settings_mismatch",
                            "; ".join(dict.fromkeys(provenance_errors)),
                        )
                    )
        manager.update(
            status=ScreeningStatus.NORMALIZING,
            stage=ScreeningStatus.NORMALIZING,
            completed_invocations=len(executions),
            failed_invocations=failure_count,
            raw_result_references=[execution.stdout_path for execution in executions],
            normalized_results=len(measurements),
            owned_processes_stopped=all_processes_stopped,
            samplers_stopped=all_samplers_stopped,
        )
        results = [
            aggregate_signature(
                signature,
                scenario_source.scenarios,
                memberships,
                executions,
                measurements,
                repetitions=config.repetitions,
                stable_maximum=config.stable_cv_maximum,
                variable_maximum=config.variable_cv_maximum,
            )
            for signature in signatures
        ]
        scored_results = score_signatures(results, plan.goal)
        manager.update(status=ScreeningStatus.SELECTING, stage=ScreeningStatus.SELECTING)
        advanced, decisions = select_candidates(
            plan.candidates,
            memberships,
            scored_results,
            config.advance_count,
        )
        if advanced:
            final_status = (
                ScreeningStatus.PARTIAL
                if failure_count or any(not item.screening_eligible for item in scored_results)
                else ScreeningStatus.COMPLETED
            )
        else:
            final_status = ScreeningStatus.FAILED
        summary = ScreeningSummary(
            screening_id=identifier,
            status=final_status,
            plan_profiles=len(plan.candidates),
            bench_signatures=len(signatures),
            scenarios=len(scenario_source.scenarios),
            expected_invocations=len(matrix),
            completed_invocations=len(executions),
            failed_invocations=failure_count,
            successful_signatures=sum(
                item.status is SignatureStatus.COMPLETED for item in scored_results
            ),
            partial_signatures=sum(
                item.status is SignatureStatus.PARTIAL for item in scored_results
            ),
            failed_signatures=sum(
                item.status
                in {
                    SignatureStatus.FAILED,
                    SignatureStatus.TIMED_OUT,
                    SignatureStatus.UNSTABLE,
                    SignatureStatus.UNSUPPORTED,
                }
                for item in scored_results
            ),
            advanced_candidates=len(advanced),
            synthetic_fixture=synthetic,
        )
        with JsonlArtifactWriter(root / "signature-results.jsonl") as writer:
            for result in scored_results:
                writer.append(result)
        with JsonlArtifactWriter(root / "advancement-decisions.jsonl") as writer:
            for decision in decisions:
                writer.append(decision)
        advanced_ids = {candidate.id for candidate in advanced}
        with JsonlArtifactWriter(root / "advanced-candidates.jsonl") as writer:
            for candidate in advanced:
                writer.append(candidate)
        with JsonlArtifactWriter(root / "non-advanced-candidates.jsonl") as writer:
            for candidate in plan.candidates:
                if candidate.id not in advanced_ids:
                    writer.append(candidate)
        for candidate in advanced:
            atomic_write_yaml(profiles / f"{candidate.id}.yaml", candidate.model_dump(mode="json"))
        atomic_write_json(root / "screening-summary.json", summary)
        manager.update(
            status=ScreeningStatus.FINALIZING,
            stage=ScreeningStatus.FINALIZING,
            failed_signatures=[
                item.signature_id for item in scored_results if not item.screening_eligible
            ],
            advancement_decisions=len(decisions),
            summary=summary,
            advanced_candidate_count=len(advanced),
        )
        validation = validate_screening_directory(root, allow_finalizing=True)
        if not validation.valid:
            manager.update(
                status=ScreeningStatus.FAILED,
                stage=ScreeningStatus.FAILED,
                error_type="ScreeningArtifactError",
                error_message="; ".join(validation.errors),
            )
            raise ScreeningArtifactError("Final screening artifacts failed validation")
        manager.update(status=final_status, stage=final_status)
        final_validation = validate_screening_directory(root)
        if not final_validation.valid:
            manager.update(
                status=ScreeningStatus.FAILED,
                stage=ScreeningStatus.FAILED,
                error_type="ScreeningArtifactError",
                error_message="; ".join(final_validation.errors),
            )
            raise ScreeningArtifactError("Final screening manifest failed validation")
        exit_code: Literal[0, 2, 3] = (
            0
            if final_status is ScreeningStatus.COMPLETED
            else 2
            if final_status is ScreeningStatus.FAILED
            else 3
        )
        return ScreeningRunResult(
            screening_id=identifier,
            output_dir=root,
            status=final_status,
            exit_code=exit_code,
            summary=summary,
        )
    except KeyboardInterrupt:
        manager.update(
            status=ScreeningStatus.INTERRUPTED,
            stage=ScreeningStatus.INTERRUPTED,
            owned_processes_stopped=True,
            samplers_stopped=True,
            error_type="KeyboardInterrupt",
            error_message="Screening interrupted by user",
        )
        return ScreeningRunResult(
            screening_id=identifier,
            output_dir=root,
            status=ScreeningStatus.INTERRUPTED,
            exit_code=3,
            summary=None,
        )
    except ScreeningArtifactError:
        raise
    except Exception as exc:
        with suppress(OSError):
            manager.update(
                status=ScreeningStatus.FAILED,
                stage=ScreeningStatus.FAILED,
                owned_processes_stopped=True,
                samplers_stopped=True,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        raise
