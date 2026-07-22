"""End-to-end reproducible execution of one fixed llama-server baseline."""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from aarchtune.baseline.artifacts import (
    JsonlArtifactWriter,
    atomic_write_json,
    prepare_run_directory,
)
from aarchtune.baseline.errors import BaselineInputError, BaselineRuntimeError
from aarchtune.baseline.manifest import ManifestManager
from aarchtune.baseline.models import (
    ArtifactReference,
    BaselineFailure,
    BaselineRunConfig,
    BaselineRunResult,
    BaselineSummary,
    ExecutionProvenance,
    FileProvenance,
    HashProvenance,
    PersistedEnvelope,
    RunStage,
    RunStatus,
)
from aarchtune.benchmark.models import ProcessMetricsSummary
from aarchtune.benchmark.request_metrics import (
    aggregate_benchmark_statistics,
    aggregate_quality,
)
from aarchtune.benchmark.runner import measure_task_attempt
from aarchtune.benchmark.system_metrics import ProcessMetricsSampler
from aarchtune.hardware.detector import detect_hardware
from aarchtune.runtime.capabilities import (
    ServerCapabilities,
    analyze_kleidiai_evidence,
    inspect_llama_server_capabilities,
)
from aarchtune.runtime.command import build_llama_server_command
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.process import LlamaServerProcess
from aarchtune.runtime.redaction import redact_environment
from aarchtune.workload.loader import load_workload, summarize_workload

_HASH_CHUNK_BYTES = 1024 * 1024


def generate_run_id(now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:6]}"


def hash_file_streaming(path: Path, chunk_bytes: int = _HASH_CHUNK_BYTES) -> str:
    """Hash a file with bounded memory; suitable for large GGUF models."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def _file_provenance(path: Path, *, synthetic: bool = False) -> FileProvenance:
    resolved = path.resolve()
    metadata = resolved.stat()
    try:
        digest = hash_file_streaming(resolved)
        hash_result = HashProvenance(value=digest, completed=True)
    except OSError as exc:
        hash_result = HashProvenance(value=None, completed=False, reason=str(exc))
    return FileProvenance(
        path=str(resolved),
        filename=resolved.name,
        size_bytes=metadata.st_size,
        modification_time_ns=metadata.st_mtime_ns,
        hash=hash_result,
        synthetic_fixture=synthetic,
    )


def _envelope(
    run_id: str,
    created_at: datetime,
    status: RunStatus,
    data: Any,
) -> PersistedEnvelope:
    serialized = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    return PersistedEnvelope(
        run_id=run_id,
        created_at=created_at,
        status=status,
        data=cast(JsonValue, serialized),
    )


def _runtime_config(config: BaselineRunConfig) -> LlamaServerConfig:
    return LlamaServerConfig(
        binary_path=config.binary_path,
        model_path=config.model_path,
        threads=config.threads,
        threads_batch=config.threads_batch,
        batch_size=config.batch_size,
        ubatch_size=config.ubatch_size,
        context_size=config.context_size,
        parallel_slots=config.parallel_slots,
        prompt_cache=config.prompt_cache,
        mmap=config.mmap,
        request_timeout_seconds=config.request_timeout_seconds,
        startup_timeout_seconds=config.startup_timeout_seconds,
        shutdown_timeout_seconds=config.shutdown_timeout_seconds,
        extra_environment=config.extra_environment,
    )


def _preflight(config: BaselineRunConfig) -> tuple[ServerCapabilities, LlamaServerConfig]:
    if not config.workload_path.expanduser().is_file():
        raise BaselineInputError(f"Workload file does not exist: {config.workload_path}")
    capabilities = inspect_llama_server_capabilities(config.binary_path, include_probe_output=True)
    runtime_config = _runtime_config(config)
    # A harmless placeholder port proves every explicit setting maps before output is changed.
    build_llama_server_command(runtime_config.model_copy(update={"port": 1}), capabilities)
    return capabilities, runtime_config


def _artifact_references() -> dict[str, ArtifactReference]:
    json_files = (
        "manifest.json",
        "hardware.json",
        "runtime-inspection.json",
        "server-command.json",
        "model.json",
        "workload.json",
        "process-summary.json",
        "quality-summary.json",
        "baseline-summary.json",
    )
    references = {
        name: ArtifactReference(path=name, media_type="application/json") for name in json_files
    }
    for name in ("raw-attempts.jsonl", "request-metrics.jsonl", "process-samples.jsonl"):
        references[name] = ArtifactReference(path=name, media_type="application/x-ndjson")
    references["server.log"] = ArtifactReference(path="server.log", media_type="text/plain")
    return references


def _write_failure(
    *,
    manager: ManifestManager,
    output_dir: Path,
    run_id: str,
    status: RunStatus,
    stage: RunStage,
    error: BaseException,
    server_stopped: bool,
    sampler_stopped: bool,
    completed_attempts: int,
) -> BaselineFailure:
    failure_status = cast(
        Any,
        status.value,
    )
    failure = BaselineFailure(
        run_id=run_id,
        status=failure_status,
        stage=stage,
        error_type=type(error).__name__,
        message=str(error),
        server_stopped=server_stopped,
        sampler_stopped=sampler_stopped,
        completed_attempt_count=completed_attempts,
    )
    atomic_write_json(output_dir / "failure.json", failure)
    manager.add_artifact("failure.json", "application/json")
    manager.update(
        status=status,
        completed_attempt_count=completed_attempts,
        server_stopped=server_stopped,
        sampler_stopped=sampler_stopped,
        error_type=type(error).__name__,
        error_message=str(error),
    )
    return failure


def run_baseline(config: BaselineRunConfig) -> BaselineRunResult:
    """Execute exactly one fixed configuration and preserve partial evidence on failure."""

    capabilities, runtime_config = _preflight(config)
    workload = load_workload(config.workload_path)
    output_dir = prepare_run_directory(config.output_dir, overwrite=config.overwrite)
    created_at = datetime.now(UTC)
    run_id = generate_run_id(created_at)
    manager = ManifestManager(output_dir, run_id, created_at)
    for name, reference in _artifact_references().items():
        manager.add_artifact(name, reference.media_type)

    execution = ExecutionProvenance(
        warmup_request_count=config.warmup_requests,
        warmup_task_ids=[],
        warmup_success=[],
        measured_repetitions=config.repetitions,
        request_timeout_seconds=config.request_timeout_seconds,
        startup_timeout_seconds=config.startup_timeout_seconds,
        process_sampling_interval_seconds=config.sample_interval_seconds,
        started_at=created_at,
    )
    attempts = []
    server: LlamaServerProcess | None = None
    sampler: ProcessMetricsSampler | None = None
    stage = RunStage.INITIALIZING
    run_started_ns = time.perf_counter_ns()
    synthetic = capabilities.version is not None and "synthetic" in capabilities.version.lower()

    try:
        stage = RunStage.INSPECTING
        manager.update(stage=stage, status=RunStatus.RUNNING)
        hardware = detect_hardware(model_path=config.model_path)
        atomic_write_json(
            output_dir / "hardware.json",
            _envelope(run_id, created_at, RunStatus.RUNNING, hardware),
        )

        stage = RunStage.HASHING
        manager.update(stage=stage)
        model_info = _file_provenance(config.model_path, synthetic=synthetic)
        binary_info = _file_provenance(config.binary_path, synthetic=synthetic)
        if not model_info.hash.completed or not binary_info.hash.completed:
            raise BaselineInputError("Could not complete mandatory binary and model hashes")
        atomic_write_json(
            output_dir / "model.json",
            _envelope(run_id, created_at, RunStatus.RUNNING, model_info),
        )
        workload_summary = summarize_workload(workload)
        atomic_write_json(
            output_dir / "workload.json",
            _envelope(run_id, created_at, RunStatus.RUNNING, workload_summary),
        )
        runtime_data: dict[str, Any] = {
            "capabilities": capabilities.model_dump(mode="json"),
            "binary": binary_info.model_dump(mode="json"),
            "exact_server_arguments": None,
            "redacted_environment_overrides": redact_environment(config.extra_environment),
        }
        atomic_write_json(
            output_dir / "runtime-inspection.json",
            _envelope(run_id, created_at, RunStatus.RUNNING, runtime_data),
        )

        # Initialize streaming artifacts before the server starts.
        (output_dir / "raw-attempts.jsonl").touch()
        (output_dir / "request-metrics.jsonl").touch()
        (output_dir / "process-samples.jsonl").touch()
        stage = RunStage.STARTING_SERVER
        manager.update(stage=stage)
        server = LlamaServerProcess(runtime_config, capabilities).start()
        if server.pid is None:
            raise BaselineRuntimeError("Owned server did not expose a process ID")
        sampler = ProcessMetricsSampler(
            server.pid,
            output_dir / "process-samples.jsonl",
            run_id,
            interval_seconds=config.sample_interval_seconds,
        )
        sampler.start()
        readiness = server.wait_until_ready()
        command = server.command
        if command is None:
            raise BaselineRuntimeError("Server command was unavailable after startup")
        atomic_write_json(
            output_dir / "server-command.json",
            _envelope(
                run_id,
                created_at,
                RunStatus.RUNNING,
                {
                    "command": command.model_dump(mode="json"),
                    "readiness": readiness.model_dump(mode="json"),
                },
            ),
        )

        stage = RunStage.WARMING_UP
        manager.update(stage=stage)
        sampler.set_phase("warmup")
        warmup_ids: list[str] = []
        warmup_success: list[bool] = []
        for index in range(config.warmup_requests):
            task = workload.tasks[index % len(workload.tasks)]
            warmup_ids.append(task.id)
            response = server.client.chat_completion(task)
            warmup_success.append(response.request_succeeded)
            if not server.is_running:
                raise BaselineRuntimeError("Server exited during warm-up")
        execution = execution.model_copy(
            update={"warmup_task_ids": warmup_ids, "warmup_success": warmup_success}
        )

        stage = RunStage.MEASURING
        manager.update(stage=stage)
        sampler.set_phase("measured")
        sampler.wait_for_phase_sample(
            "measured", timeout_seconds=max(1.0, config.sample_interval_seconds * 3)
        )
        measured_started_at = datetime.now(UTC)
        measured_started_ns = time.perf_counter_ns()
        consecutive_failures = 0
        with (
            JsonlArtifactWriter(output_dir / "raw-attempts.jsonl") as raw_writer,
            JsonlArtifactWriter(output_dir / "request-metrics.jsonl") as metrics_writer,
        ):
            for repetition in range(1, config.repetitions + 1):
                for task_index, task in enumerate(workload.tasks):
                    attempt = measure_task_attempt(
                        client=server.client,
                        workload=workload,
                        task=task,
                        run_id=run_id,
                        repetition=repetition,
                        task_index=task_index,
                    )
                    raw_writer.append(attempt.raw)
                    metrics_writer.append(attempt.measurement)
                    attempts.append(attempt)
                    manager.update(completed_attempt_count=len(attempts))
                    if attempt.measurement.execution.request_succeeded:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if not server.is_running:
                            raise BaselineRuntimeError("Server exited during measured execution")
                        probe = server.client.get_readiness("/health", timeout_seconds=0.25)
                        if not probe.succeeded:
                            raise BaselineRuntimeError(
                                f"Readiness was lost after request failure: {probe.error}"
                            )
                        if (
                            consecutive_failures
                            >= config.maximum_consecutive_infrastructure_failures
                        ):
                            raise BaselineRuntimeError(
                                "Repeated infrastructure failures reached the configured limit"
                            )
        measured_ended_ns = time.perf_counter_ns()
        measured_ended_at = datetime.now(UTC)
        measured_interval = (measured_ended_ns - measured_started_ns) / 1_000_000_000
        execution = execution.model_copy(
            update={
                "measured_started_at": measured_started_at,
                "measured_ended_at": measured_ended_at,
                "measured_interval_seconds": measured_interval,
            }
        )

        stage = RunStage.EVALUATING
        manager.update(stage=stage)
        benchmark = aggregate_benchmark_statistics(
            run_id,
            attempts,
            configured_attempts=len(workload.tasks) * config.repetitions,
            measured_interval_seconds=measured_interval,
        )
        quality = aggregate_quality(run_id, attempts)

        stage = RunStage.FINALIZING
        manager.update(stage=stage)
        sampler.set_phase("shutdown")
        sampler.stop()
        shutdown = server.stop()
        process_summary = sampler.summary()
        ended_at = datetime.now(UTC)
        execution = execution.model_copy(
            update={
                "ended_at": ended_at,
                "total_duration_seconds": (time.perf_counter_ns() - run_started_ns) / 1_000_000_000,
            }
        )
        startup_evidence = analyze_kleidiai_evidence(server.log_text)
        if startup_evidence.status.value != "unknown":
            runtime_data["kleidiai_status"] = startup_evidence.status.value
            runtime_data["kleidiai_evidence"] = startup_evidence.evidence
        else:
            runtime_data["kleidiai_status"] = capabilities.kleidiai_status.value
            runtime_data["kleidiai_evidence"] = capabilities.kleidiai_evidence
        runtime_data["exact_server_arguments"] = command.arguments
        runtime_data["shutdown"] = shutdown.model_dump(mode="json")
        atomic_write_json(
            output_dir / "runtime-inspection.json",
            _envelope(run_id, created_at, RunStatus.COMPLETED, runtime_data),
        )
        (output_dir / "server.log").write_text(server.log_text, encoding="utf-8")
        atomic_write_json(
            output_dir / "process-summary.json",
            _envelope(run_id, created_at, RunStatus.COMPLETED, process_summary),
        )
        atomic_write_json(
            output_dir / "quality-summary.json",
            _envelope(run_id, created_at, RunStatus.COMPLETED, quality),
        )
        summary = BaselineSummary(
            run_id=run_id,
            created_at=created_at,
            status=RunStatus.COMPLETED,
            synthetic_fixture=synthetic,
            platform_architecture=hardware.architecture,
            is_arm64=hardware.is_arm64,
            runtime_version=capabilities.version,
            kleidiai_status=str(runtime_data["kleidiai_status"]),
            workload_task_count=len(workload.tasks),
            repetitions=config.repetitions,
            execution=execution,
            benchmark=benchmark,
            quality=quality,
            process=process_summary,
            artifacts=manager.manifest.artifacts,
        )
        atomic_write_json(output_dir / "baseline-summary.json", summary)
        stage = RunStage.COMPLETED
        manager.update(
            stage=stage,
            status=RunStatus.COMPLETED,
            completed_attempt_count=len(attempts),
            server_stopped=shutdown.stopped,
            sampler_stopped=not sampler.thread_alive,
        )
        return BaselineRunResult(
            run_id=run_id,
            output_dir=output_dir,
            status=RunStatus.COMPLETED,
            exit_code=0,
            summary=summary,
        )
    except KeyboardInterrupt as exc:
        status = RunStatus.INTERRUPTED
        server_stopped = server.stop().stopped if server is not None else True
        if sampler is not None:
            sampler.stop()
        sampler_stopped = sampler is None or not sampler.thread_alive
        if server is not None:
            (output_dir / "server.log").write_text(server.log_text, encoding="utf-8")
        failure = _write_failure(
            manager=manager,
            output_dir=output_dir,
            run_id=run_id,
            status=status,
            stage=stage,
            error=exc,
            server_stopped=server_stopped,
            sampler_stopped=sampler_stopped,
            completed_attempts=len(attempts),
        )
        return BaselineRunResult(
            run_id=run_id,
            output_dir=output_dir,
            status=status,
            exit_code=3,
            failure=failure,
        )
    except Exception as exc:
        status = RunStatus.PARTIAL if attempts else RunStatus.FAILED
        server_stopped = server.stop().stopped if server is not None else True
        if sampler is not None:
            sampler.stop()
        sampler_stopped = sampler is None or not sampler.thread_alive
        if server is not None:
            (output_dir / "server.log").write_text(server.log_text, encoding="utf-8")
        if sampler is not None:
            partial_process_summary: ProcessMetricsSummary = sampler.summary()
            atomic_write_json(
                output_dir / "process-summary.json",
                _envelope(run_id, created_at, status, partial_process_summary),
            )
        if attempts:
            partial_quality = aggregate_quality(run_id, attempts)
            atomic_write_json(
                output_dir / "quality-summary.json",
                _envelope(run_id, created_at, status, partial_quality),
            )
        failure = _write_failure(
            manager=manager,
            output_dir=output_dir,
            run_id=run_id,
            status=status,
            stage=stage,
            error=exc,
            server_stopped=server_stopped,
            sampler_stopped=sampler_stopped,
            completed_attempts=len(attempts),
        )
        return BaselineRunResult(
            run_id=run_id,
            output_dir=output_dir,
            status=status,
            exit_code=3 if status is RunStatus.PARTIAL else 2,
            failure=failure,
        )
