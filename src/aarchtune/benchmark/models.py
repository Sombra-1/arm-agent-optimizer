"""Strict models for raw attempts and aggregate baseline measurements."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from aarchtune.workload.schema import TaskEvaluationResult, ValidatorType


class BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OptionalMetric(BenchmarkModel):
    """Numeric value whose absence is explicit rather than encoded as zero."""

    value: int | float | None
    available: bool
    reason: str | None = None
    source: Literal["server_reported", "client_derived", "process_sampled"] | None = None
    source_path: str | None = None


def unavailable_metric(reason: str) -> OptionalMetric:
    return OptionalMetric(value=None, available=False, reason=reason)


class NormalizedServerMetrics(BenchmarkModel):
    prompt_tokens: OptionalMetric
    completion_tokens: OptionalMetric
    total_tokens: OptionalMetric
    prompt_processing_seconds: OptionalMetric
    generation_seconds: OptionalMetric
    server_prompt_tokens_per_second: OptionalMetric
    server_generation_tokens_per_second: OptionalMetric
    client_prompt_tokens_per_second: OptionalMetric
    client_completion_tokens_per_second: OptionalMetric
    time_to_first_token_seconds: OptionalMetric
    raw_fields: dict[str, JsonValue] = Field(default_factory=dict)


class RequestDescription(BenchmarkModel):
    temperature: float
    max_tokens: int
    seed: int | None
    stream: Literal[False] = False


class ExecutionResult(BenchmarkModel):
    request_succeeded: bool
    timed_out: bool
    status_code: int | None
    error: str | None
    failure_kind: str | None = None


class TimingMeasurement(BenchmarkModel):
    started_at: datetime
    finished_at: datetime
    duration_ns: int
    duration_seconds: float


class RequestMeasurement(BenchmarkModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["completed"] = "completed"
    attempt_id: str
    repetition: int
    task_index: int
    task_id: str
    category: str
    request: RequestDescription
    execution: ExecutionResult
    timing: TimingMeasurement
    tokens: NormalizedServerMetrics
    raw_attempt_reference: str


class RawAttemptRecord(BenchmarkModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["completed"] = "completed"
    attempt_id: str
    repetition: int
    task_index: int
    task_id: str
    category: str
    response_text: str
    execution: ExecutionResult
    server_fields: dict[str, JsonValue] = Field(default_factory=dict)
    validation: TaskEvaluationResult


class TaskAttemptResult(BenchmarkModel):
    measurement: RequestMeasurement
    raw: RawAttemptRecord


class NumericStatistics(BenchmarkModel):
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    median: float | None
    p95: float | None
    standard_deviation: float | None
    unavailable_reason: str | None = None


class ProcessSample(BenchmarkModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["running"] = "running"
    timestamp: datetime
    monotonic_ns: int
    phase: Literal["startup", "warmup", "measured", "shutdown"]
    pid: int
    rss_bytes: int
    vms_bytes: int
    cpu_percent: float
    user_cpu_seconds: float
    system_cpu_seconds: float
    thread_count: int
    child_process_count: int
    aggregate_rss_bytes: int


class ProcessMetricsSummary(BenchmarkModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    sample_count: int
    whole_run_peak_rss_bytes: OptionalMetric
    measured_phase_peak_rss_bytes: OptionalMetric
    mean_measured_rss_bytes: OptionalMetric
    peak_vms_bytes: OptionalMetric
    mean_cpu_percent: OptionalMetric
    peak_cpu_percent: OptionalMetric
    user_cpu_seconds_delta: OptionalMetric
    system_cpu_seconds_delta: OptionalMetric
    maximum_thread_count: OptionalMetric
    maximum_child_process_count: OptionalMetric
    sampling_errors: list[str] = Field(default_factory=list)


class BenchmarkStatistics(BenchmarkModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    total_configured_attempts: int
    measured_attempts_completed: int
    successful_requests: int
    failed_requests: int
    timeout_count: int
    http_failure_count: int
    invalid_response_failure_count: int
    measured_interval_seconds: float
    requests_per_minute: OptionalMetric
    latency_seconds: NumericStatistics
    prompt_tokens: NumericStatistics
    completion_tokens: NumericStatistics
    server_prompt_tokens_per_second: NumericStatistics
    server_generation_tokens_per_second: NumericStatistics
    client_prompt_tokens_per_second: NumericStatistics
    client_completion_tokens_per_second: NumericStatistics
    prompt_tokens_processed: int
    completion_tokens_generated: int


class QualityGroupStatistics(BenchmarkModel):
    attempts: int
    passed: int
    failed: int
    success_rate: float | None


class ValidatorQualityStatistics(BenchmarkModel):
    total: int
    passed: int
    failed: int
    pass_rate: float | None


class BaselineQualitySummary(BenchmarkModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    task_attempts: int
    passed_task_attempts: int
    failed_task_attempts: int
    task_attempt_success_rate: float | None
    unique_tasks_passing_every_repetition: list[str]
    unique_tasks_failing_at_least_once: list[str]
    validator_pass_count: int
    validator_failure_count: int
    validator_pass_rate: float | None
    json_valid_response_count: int
    json_validity_rate: float | None
    request_success_count: int
    request_success_rate: float | None
    timeout_count: int
    timeout_rate: float | None
    per_category: dict[str, QualityGroupStatistics]
    per_validator_type: dict[ValidatorType, ValidatorQualityStatistics]
    unavailable_reasons: dict[str, str] = Field(default_factory=dict)
