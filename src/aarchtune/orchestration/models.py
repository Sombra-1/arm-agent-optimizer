"""Strict orchestration configuration, stage, manifest, and result models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aarchtune.optimization.models import OptimizationGoal


class OrchestrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"


class OptimizeStage(StrEnum):
    INITIALIZING = "initializing"
    DOCTOR = "doctor"
    BASELINE = "baseline"
    PLANNING = "planning"
    SCREENING = "screening"
    EVALUATION = "evaluation"
    FINALIZATION = "finalization"
    VALIDATING = "validating"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class OptimizeStageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class OptimizeConfig(OrchestrationModel):
    server_binary: Path
    bench_binary: Path
    model: Path
    workload: Path
    goal: OptimizationGoal = OptimizationGoal.BALANCED
    output_dir: Path
    search_space: Path | None = None
    screening_scenarios: Path | None = None
    quality_policy: Path | None = None
    container_image: str | None = None
    baseline_repetitions: Annotated[int, Field(ge=1, le=100)] = 2
    evaluation_repetitions: Annotated[int, Field(ge=1, le=100)] = 3
    warmup_requests: Annotated[int, Field(ge=0, le=100)] = 1
    advance_count: Annotated[int, Field(ge=1, le=24)] = 6
    max_profiles: Annotated[int, Field(ge=1, le=64)] | None = None
    screening_repetitions: Annotated[int, Field(ge=1, le=20)] = 3
    request_timeout_seconds: Annotated[float, Field(ge=0.1, le=600.0)] = 60.0
    startup_timeout_seconds: Annotated[float, Field(ge=0.1, le=600.0)] = 30.0
    sample_interval_seconds: Annotated[float, Field(ge=0.05, le=5.0)] = 0.1
    maximum_total_duration_seconds: Annotated[float, Field(ge=1.0, le=86400.0)] = 7200.0
    allow_synthetic: bool = False
    allow_non_arm_development: bool = False
    allow_runtime_change: bool = False
    overwrite: bool = False
    resume: bool = False

    @model_validator(mode="after")
    def mutually_exclusive_output_modes(self) -> OptimizeConfig:
        if self.overwrite and self.resume:
            raise ValueError("--overwrite and --resume cannot be used together")
        return self


class StageReference(OrchestrationModel):
    stage: OptimizeStage
    status: OptimizeStageStatus
    path: str
    identity: str | None
    manifest_sha256: str | None
    reused: bool
    validation_passed: bool


class ResumeAssessment(OrchestrationModel):
    resumable: bool
    first_stage_to_run: OptimizeStage
    reused_stages: list[OptimizeStage]
    decisions: list[str]


class OptimizeFailure(OrchestrationModel):
    stage: OptimizeStage
    error_type: str
    message: str


class OptimizeManifest(OrchestrationModel):
    optimize_id: str
    created_at: datetime
    updated_at: datetime
    status: OptimizeStageStatus
    active_stage: OptimizeStage
    output_directory: Path
    configuration_hash: str
    input_fingerprint: dict[str, str]
    stages: list[StageReference] = Field(default_factory=list)
    resume_decisions: list[str] = Field(default_factory=list)
    owned_processes_stopped: bool | None = None
    samplers_stopped: bool | None = None
    failure: OptimizeFailure | None = None


class OptimizeRunResult(OrchestrationModel):
    optimize_id: str
    output_dir: Path
    status: OptimizeStageStatus
    exit_code: Literal[0, 1, 2, 3, 4]
    outcome: str | None
    selected_profile_id: str | None
    final_dir: Path | None
    resumed: bool
    reused_stages: list[OptimizeStage]


class OptimizeValidationResult(OrchestrationModel):
    valid: bool
    optimize_id: str | None
    errors: list[str]
    warnings: list[str]
