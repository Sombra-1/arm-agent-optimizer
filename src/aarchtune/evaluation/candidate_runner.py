"""Isolated execution of one profile through the existing baseline primitive."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from aarchtune.baseline.models import BaselineRunConfig, RunStatus
from aarchtune.baseline.runner import run_baseline
from aarchtune.benchmark.models import RawAttemptRecord, unavailable_metric
from aarchtune.evaluation.models import (
    CandidateConsistency,
    CandidateExecutionResult,
    CandidateExecutionStatus,
    CandidatePerformanceSummary,
    CandidateQualitySummary,
    EvaluationConfig,
)
from aarchtune.optimization.models import CandidateProfile


def _value(metric: Any) -> float | None:
    value = metric.value if metric.available else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _coefficient(values: list[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value) and value >= 0]
    if len(finite) < 2:
        return None
    mean = statistics.fmean(finite)
    return statistics.stdev(finite) / mean if mean > 0 else None


def _consistency(run_directory: Path, repetitions: int) -> CandidateConsistency:
    records = [
        RawAttemptRecord.model_validate_json(line)
        for line in (run_directory / "raw-attempts.jsonl").read_text().splitlines()
        if line
    ]
    by_task: dict[str, list[bool]] = {}
    for record in records:
        by_task.setdefault(record.task_id, []).append(record.validation.passed is True)
    passing = sum(len(values) == repetitions and all(values) for values in by_task.values())
    failing = sum(not all(values) for values in by_task.values())
    inconsistent = sum(any(values) and not all(values) for values in by_task.values())
    consistent = sum(len(set(values)) == 1 for values in by_task.values())
    durations: list[float] = []
    throughput: list[float] = []
    metrics_path = run_directory / "request-metrics.jsonl"
    for line in metrics_path.read_text().splitlines():
        if not line:
            continue
        raw = json.loads(line)
        duration = raw.get("timing", {}).get("duration_seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            durations.append(float(duration))
        value = raw.get("tokens", {}).get("server_generation_tokens_per_second", {})
        observed = value.get("value") if value.get("available") is True else None
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            throughput.append(float(observed))
    return CandidateConsistency(
        tasks_passing_every_repetition=passing,
        tasks_failing_at_least_once=failing,
        inconsistent_task_count=inconsistent,
        per_task_pass_consistency_rate=(consistent / len(by_task) if by_task else None),
        latency_coefficient_of_variation=_coefficient(durations),
        throughput_coefficient_of_variation=_coefficient(throughput),
    )


def _performance(summary: Any) -> CandidatePerformanceSummary:
    benchmark = summary.benchmark
    process = summary.process
    quality = summary.quality
    return CandidatePerformanceSummary(
        configured_attempts=benchmark.total_configured_attempts,
        completed_attempts=benchmark.measured_attempts_completed,
        successful_requests=benchmark.successful_requests,
        request_success_rate=quality.request_success_rate,
        median_latency_seconds=benchmark.latency_seconds.median,
        p95_latency_seconds=benchmark.latency_seconds.p95,
        mean_latency_seconds=benchmark.latency_seconds.mean,
        requests_per_minute=_value(benchmark.requests_per_minute),
        prompt_tokens_total=benchmark.prompt_tokens_processed,
        completion_tokens_total=benchmark.completion_tokens_generated,
        server_prompt_throughput=benchmark.server_prompt_tokens_per_second.mean,
        server_generation_throughput=benchmark.server_generation_tokens_per_second.mean,
        measured_peak_rss_bytes=(
            int(value)
            if (value := _value(process.measured_phase_peak_rss_bytes)) is not None
            else None
        ),
        whole_run_peak_rss_bytes=(
            int(value) if (value := _value(process.whole_run_peak_rss_bytes)) is not None else None
        ),
        mean_measured_rss_bytes=_value(process.mean_measured_rss_bytes),
        mean_cpu_percent=_value(process.mean_cpu_percent),
        peak_cpu_percent=_value(process.peak_cpu_percent),
        time_to_first_token=unavailable_metric(
            "Non-streaming evaluation does not measure time to first token"
        ),
        comparable=True,
        comparability_reasons=[],
    )


def execute_candidate(
    *,
    profile: CandidateProfile,
    label: str,
    run_directory: Path,
    config: EvaluationConfig,
    model_path: Path,
    workload_path: Path,
    screening_score: float | None,
) -> CandidateExecutionResult:
    runtime = profile.runtime
    if runtime.numa_mode != "disabled" or runtime.cpu_affinity_policy != "none":
        return CandidateExecutionResult(
            candidate_id=profile.id,
            candidate_hash=profile.profile_hash,
            label=label,
            profile=profile,
            status=CandidateExecutionStatus.UNSUPPORTED,
            run_id=f"unsupported-{label}",
            run_directory=run_directory,
            screening_score=screening_score,
            performance=None,
            quality=None,
            failure_type="unsupported_configuration",
            failure_message="NUMA and affinity execution mappings are unavailable in v1",
            server_stopped=True,
            sampler_stopped=True,
        )
    baseline_config = BaselineRunConfig(
        binary_path=runtime.binary_path,
        model_path=model_path,
        workload_path=workload_path,
        output_dir=run_directory,
        repetitions=config.repetitions,
        warmup_requests=config.warmup_requests,
        threads=runtime.threads,
        threads_batch=runtime.threads_batch,
        batch_size=runtime.batch_size,
        ubatch_size=runtime.ubatch_size,
        context_size=runtime.context_size,
        parallel_slots=runtime.parallel_slots,
        prompt_cache=runtime.prompt_cache,
        mmap=runtime.mmap,
        request_timeout_seconds=config.request_timeout_seconds,
        startup_timeout_seconds=config.startup_timeout_seconds,
        shutdown_timeout_seconds=config.shutdown_timeout_seconds,
        sample_interval_seconds=config.sample_interval_seconds,
        overwrite=False,
        extra_environment={"AARCHTUNE_EVALUATION_RUN_LABEL": label},
    )
    result = run_baseline(baseline_config)
    manifest_data = json.loads((run_directory / "manifest.json").read_text())
    server_stopped = manifest_data.get("server_stopped") is True
    sampler_stopped = manifest_data.get("sampler_stopped") is True
    if result.summary is not None:
        performance = _performance(result.summary)
        quality = CandidateQualitySummary(
            aggregate=result.summary.quality,
            consistency=_consistency(run_directory, config.repetitions),
        )
        status = CandidateExecutionStatus.COMPLETED
        failure_type = None
        failure_message = None
    else:
        performance = None
        quality = None
        if result.status is RunStatus.INTERRUPTED:
            status = CandidateExecutionStatus.INTERRUPTED
        elif result.status is RunStatus.PARTIAL:
            status = CandidateExecutionStatus.PARTIAL
        elif result.failure and "timeout" in result.failure.message.lower():
            status = CandidateExecutionStatus.TIMED_OUT
        else:
            status = CandidateExecutionStatus.FAILED
        failure_type = result.failure.error_type if result.failure else "execution_failure"
        failure_message = result.failure.message if result.failure else "Execution failed"
    return CandidateExecutionResult(
        candidate_id=profile.id,
        candidate_hash=profile.profile_hash,
        label=label,
        profile=profile,
        status=status,
        run_id=result.run_id,
        run_directory=run_directory,
        screening_score=screening_score,
        performance=performance,
        quality=quality,
        failure_type=failure_type,
        failure_message=failure_message,
        server_stopped=server_stopped,
        sampler_stopped=sampler_stopped,
    )
