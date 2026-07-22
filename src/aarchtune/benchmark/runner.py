"""Shared single-task measurement helper."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from aarchtune.benchmark.models import (
    ExecutionResult,
    RawAttemptRecord,
    RequestDescription,
    RequestMeasurement,
    TaskAttemptResult,
    TimingMeasurement,
)
from aarchtune.benchmark.normalization import normalize_server_metrics
from aarchtune.runtime.client import LlamaServerClient
from aarchtune.workload.evaluation import evaluate_workload
from aarchtune.workload.schema import LoadedWorkload, WorkloadTask


def _failure_kind(error: str | None) -> str | None:
    return error.partition(":")[0] if error and ":" in error else None


def measure_task_attempt(
    *,
    client: LlamaServerClient,
    workload: LoadedWorkload,
    task: WorkloadTask,
    run_id: str,
    repetition: int,
    task_index: int,
) -> TaskAttemptResult:
    """Measure with monotonic nanoseconds, then evaluate without interpreting output."""

    started_at = datetime.now(UTC)
    started_ns = time.perf_counter_ns()
    detailed = client.chat_completion_detailed(task)
    finished_ns = time.perf_counter_ns()
    finished_at = datetime.now(UTC)
    duration_ns = finished_ns - started_ns
    response = detailed.response
    reduced = workload.model_copy(update={"tasks": [task]})
    validation = evaluate_workload(reduced, [response]).task_results[0]
    attempt_id = f"r{repetition:04d}-t{task_index:04d}-{task.id}"
    execution = ExecutionResult(
        request_succeeded=response.request_succeeded,
        timed_out=response.timed_out,
        status_code=response.status_code,
        error=response.error,
        failure_kind=_failure_kind(response.error),
    )
    metrics = normalize_server_metrics(detailed.raw_json, duration_ns)
    measurement = RequestMeasurement(
        run_id=run_id,
        attempt_id=attempt_id,
        repetition=repetition,
        task_index=task_index,
        task_id=task.id,
        category=task.category,
        request=RequestDescription(
            temperature=task.generation.temperature,
            max_tokens=task.generation.max_tokens,
            seed=task.generation.seed,
        ),
        execution=execution,
        timing=TimingMeasurement(
            started_at=started_at,
            finished_at=finished_at,
            duration_ns=duration_ns,
            duration_seconds=duration_ns / 1_000_000_000,
        ),
        tokens=metrics,
        raw_attempt_reference=f"raw-attempts.jsonl#{attempt_id}",
    )
    raw = RawAttemptRecord(
        run_id=run_id,
        attempt_id=attempt_id,
        repetition=repetition,
        task_index=task_index,
        task_id=task.id,
        category=task.category,
        response_text=response.text,
        execution=execution,
        server_fields=metrics.raw_fields,
        validation=validation,
    )
    return TaskAttemptResult(measurement=measurement, raw=raw)
