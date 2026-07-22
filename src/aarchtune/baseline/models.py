"""Strict baseline configuration, provenance, manifest, and result models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from aarchtune.benchmark.models import (
    BaselineQualitySummary,
    BenchmarkStatistics,
    ProcessMetricsSummary,
)


class BaselineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunStatus(StrEnum):
    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RunStage(StrEnum):
    INITIALIZING = "initializing"
    INSPECTING = "inspecting"
    HASHING = "hashing"
    STARTING_SERVER = "starting_server"
    WARMING_UP = "warming_up"
    MEASURING = "measuring"
    EVALUATING = "evaluating"
    FINALIZING = "finalizing"
    COMPLETED = "completed"


class BaselineRunConfig(BaselineModel):
    binary_path: Path
    model_path: Path
    workload_path: Path
    output_dir: Path
    repetitions: Annotated[int, Field(ge=1, le=100)] = 1
    warmup_requests: Annotated[int, Field(ge=0, le=100)] = 1
    threads: Annotated[int, Field(ge=1, le=4096)] | None = None
    threads_batch: Annotated[int, Field(ge=1, le=4096)] | None = None
    batch_size: Annotated[int, Field(ge=1, le=1_048_576)] | None = None
    ubatch_size: Annotated[int, Field(ge=1, le=1_048_576)] | None = None
    context_size: Annotated[int, Field(ge=1, le=1_048_576)] | None = None
    parallel_slots: Annotated[int, Field(ge=1, le=1024)] | None = None
    prompt_cache: bool = False
    mmap: bool = True
    request_timeout_seconds: Annotated[float, Field(ge=0.1, le=600.0)] = 60.0
    startup_timeout_seconds: Annotated[float, Field(ge=0.1, le=600.0)] = 30.0
    shutdown_timeout_seconds: Annotated[float, Field(ge=0.1, le=60.0)] = 5.0
    sample_interval_seconds: Annotated[float, Field(ge=0.05, le=5.0)] = 0.1
    overwrite: bool = False
    maximum_consecutive_infrastructure_failures: Annotated[int, Field(ge=1, le=100)] = 3
    extra_environment: dict[str, str] = Field(default_factory=dict)


class RunIdentity(BaselineModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime


class ArtifactReference(BaselineModel):
    path: str
    media_type: str
    required: bool = True


class HashProvenance(BaselineModel):
    algorithm: Literal["sha256"] = "sha256"
    value: str | None
    completed: bool
    reason: str | None = None


class FileProvenance(BaselineModel):
    path: str
    filename: str
    size_bytes: int
    modification_time_ns: int
    hash: HashProvenance
    synthetic_fixture: bool = False


class ExecutionProvenance(BaselineModel):
    warmup_request_count: int
    warmup_task_ids: list[str]
    warmup_success: list[bool]
    measured_repetitions: int
    request_timeout_seconds: float
    startup_timeout_seconds: float
    process_sampling_interval_seconds: float
    started_at: datetime
    ended_at: datetime | None = None
    total_duration_seconds: float | None = None
    measured_started_at: datetime | None = None
    measured_ended_at: datetime | None = None
    measured_interval_seconds: float | None = None


class BaselineFailure(BaselineModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["failed", "partial", "interrupted"]
    stage: RunStage
    error_type: str
    message: str
    server_stopped: bool
    sampler_stopped: bool
    completed_attempt_count: int


class BaselineManifest(BaselineModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime
    status: RunStatus
    stage: RunStage
    updated_at: datetime
    output_directory: str
    completed_attempt_count: int = 0
    server_stopped: bool | None = None
    sampler_stopped: bool | None = None
    artifacts: dict[str, ArtifactReference] = Field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None


class BaselineSummary(BaselineModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime
    status: RunStatus
    synthetic_fixture: bool
    platform_architecture: str
    is_arm64: bool
    runtime_version: str | None
    kleidiai_status: str
    workload_task_count: int
    repetitions: int
    execution: ExecutionProvenance
    benchmark: BenchmarkStatistics
    quality: BaselineQualitySummary
    process: ProcessMetricsSummary
    artifacts: dict[str, ArtifactReference]


class PersistedEnvelope(BaselineModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime
    status: RunStatus
    data: JsonValue


class BaselineRunResult(BaselineModel):
    run_id: str
    output_dir: Path
    status: RunStatus
    exit_code: Literal[0, 2, 3]
    summary: BaselineSummary | None = None
    failure: BaselineFailure | None = None


def json_value(value: Any) -> JsonValue:
    """Validate an already serialized value as JSON-compatible data."""

    return PersistedEnvelope.model_validate(
        {
            "run_id": "validation",
            "created_at": datetime.now(UTC),
            "status": RunStatus.RUNNING,
            "data": value,
        }
    ).data
