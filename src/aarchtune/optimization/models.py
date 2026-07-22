"""Strict schemas for search spaces, candidates, provenance, and plans."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from aarchtune.models import CPUFeatures


class OptimizationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"


class OptimizationGoal(StrEnum):
    LATENCY = "latency"
    THROUGHPUT = "throughput"
    MEMORY = "memory"
    BALANCED = "balanced"


class CompatibilityClass(StrEnum):
    IDENTICAL = "identical"
    COMPATIBLE_WITH_WARNINGS = "compatible_with_warnings"
    INCOMPATIBLE = "incompatible"


class CandidateCompatibilityClass(StrEnum):
    COMPATIBLE = "compatible"
    COMPATIBLE_WITH_WARNINGS = "compatible_with_warnings"
    INCOMPATIBLE = "incompatible"


class MemoryRiskClass(StrEnum):
    SAFE = "safe"
    WARNING = "warning"
    HIGH_RISK = "high_risk"
    UNKNOWN = "unknown"


class ParameterSourceKind(StrEnum):
    BASELINE = "baseline"
    FRACTION_PHYSICAL = "fraction_of_physical_cores"
    FRACTION_LOGICAL = "fraction_of_logical_cores"
    GOAL_SPECIFIC = "goal_specific"
    SEARCH_SPACE = "search_space"
    USER_OVERRIDE = "user_override"
    RUNTIME_DEFAULT = "runtime_default"


class CandidateParameterSource(OptimizationModel):
    source: ParameterSourceKind
    detail: str


class ProfileRuntime(OptimizationModel):
    backend_label: str
    binary_path: Path
    threads: int | None = None
    threads_batch: int | None = None
    batch_size: int | None = None
    ubatch_size: int | None = None
    context_size: int | None = None
    parallel_slots: int | None = None
    prompt_cache: bool = False
    mmap: bool = True
    numa_mode: Literal["disabled", "distribute", "isolate", "numactl"] = "disabled"
    cpu_affinity_policy: Literal["none", "compact", "spread"] = "none"

    @model_validator(mode="after")
    def validate_relationships(self) -> ProfileRuntime:
        positive = (
            self.threads,
            self.threads_batch,
            self.batch_size,
            self.ubatch_size,
            self.context_size,
            self.parallel_slots,
        )
        if any(value is not None and value <= 0 for value in positive):
            raise ValueError("runtime numeric values must be positive")
        if (
            self.batch_size is not None
            and self.ubatch_size is not None
            and self.ubatch_size > self.batch_size
        ):
            raise ValueError("ubatch_size cannot exceed batch_size")
        return self


class CompatibilityDetail(OptimizationModel):
    field: str
    requested_value: JsonValue
    required_flag: str | None
    supported: bool
    reason: str


class CandidateCompatibility(OptimizationModel):
    classification: CandidateCompatibilityClass
    compatible: bool
    unsupported_flags: list[str]
    warnings: list[str]
    details: list[CompatibilityDetail]


class CandidateResourceEstimate(OptimizationModel):
    classification: MemoryRiskClass
    estimated_memory_bytes: int | None
    available: bool
    method: str
    inputs: dict[str, JsonValue]
    assumptions: list[str]
    confidence: Literal["none", "low", "medium"]
    reason: str


class CandidateProfile(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str
    profile_hash: str
    stage: str
    baseline: bool = False
    executable: bool
    goal_tags: list[OptimizationGoal]
    runtime: ProfileRuntime
    parameter_sources: dict[str, CandidateParameterSource]
    rationale: list[str]
    compatibility: CandidateCompatibility
    resource_estimate: CandidateResourceEstimate

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if (
            not value
            or len(value) > 100
            or not all(
                character.islower() or character.isdigit() or character == "-"
                for character in value
            )
        ):
            raise ValueError("candidate ID must be lowercase letters, digits, and hyphens")
        return value


class PlanningExclusion(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    stage: str
    candidate_id: str | None = None
    profile_hash: str | None = None
    reason_code: str
    reason: str
    proposed_runtime: ProfileRuntime | None = None
    compatibility_details: list[CompatibilityDetail] = Field(default_factory=list)


class PlanningWarning(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    code: str
    message: str


class HardwareFingerprint(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    architecture: str
    is_arm64: bool
    cpu_model: str | None
    logical_cores: int | None
    physical_cores: int | None
    total_memory_bytes: int | None
    available_memory_bytes: int | None
    numa_nodes: int | None
    features: CPUFeatures
    synthetic_fixture: bool = False
    fingerprint_hash: str


class RuntimeFingerprint(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    binary_path: Path
    binary_sha256: str
    binary_size: int
    binary_mtime_ns: int
    version: str | None
    supported_flags: list[str]
    kleidiai_status: str
    fingerprint_hash: str


class ModelFingerprint(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    path: Path
    filename: str
    size_bytes: int
    sha256: str
    synthetic_fixture: bool


class WorkloadFingerprint(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    path: Path
    sha256: str
    task_count: int
    category_count: int
    validator_count: int
    deterministic: bool


class CompatibilityDifference(OptimizationModel):
    field: str
    baseline_value: JsonValue
    current_value: JsonValue
    severity: Literal["warning", "incompatible", "overridden"]
    reason: str


class ProvenanceCompatibility(OptimizationModel):
    classification: CompatibilityClass
    differences: list[CompatibilityDifference]
    overrides: list[str]


class BaselineReference(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    path: Path
    run_id: str
    status: str
    synthetic_fixture: bool
    manifest_sha256: str
    compatibility: ProvenanceCompatibility


class SearchSpaceLimits(OptimizationModel):
    minimum_profiles: Annotated[int, Field(ge=1, le=64)]
    maximum_profiles: Annotated[int, Field(ge=1, le=64)]

    @model_validator(mode="after")
    def validate_order(self) -> SearchSpaceLimits:
        if self.minimum_profiles > self.maximum_profiles:
            raise ValueError("minimum_profiles cannot exceed maximum_profiles")
        return self


class ThreadSpace(OptimizationModel):
    fractions_of_physical_cores: list[Annotated[float, Field(gt=0.0, le=1.0)]]
    include_baseline: bool = True

    @field_validator("fractions_of_physical_cores")
    @classmethod
    def unique_fractions(cls, values: list[float]) -> list[float]:
        if not values or len(values) != len(set(values)):
            raise ValueError("thread fractions must be non-empty and unique")
        return values


class ThreadsBatchSpace(ThreadSpace):
    allow_greater_than_generation_threads: bool = True


class ContextSpace(OptimizationModel):
    policy: Literal["baseline", "explicit"] = "baseline"
    explicit_sizes: list[Annotated[int, Field(ge=128, le=1_048_576)]] = Field(default_factory=list)

    @model_validator(mode="after")
    def explicit_requires_sizes(self) -> ContextSpace:
        if self.policy == "explicit" and not self.explicit_sizes:
            raise ValueError("explicit context policy requires explicit_sizes")
        if len(self.explicit_sizes) != len(set(self.explicit_sizes)):
            raise ValueError("context sizes must be unique")
        return self


class SearchSpaceConfig(OptimizationModel):
    schema_version: Literal["1.0"]
    limits: SearchSpaceLimits
    threads: ThreadSpace
    threads_batch: ThreadsBatchSpace
    batch_sizes: list[Annotated[int, Field(ge=1, le=1_048_576)]]
    ubatch_sizes: list[Annotated[int, Field(ge=1, le=1_048_576)]]
    parallel_slots: list[Annotated[int, Field(ge=1, le=64)]]
    prompt_cache: list[bool]
    mmap: list[bool]
    numa_modes: list[Literal["disabled", "distribute", "isolate", "numactl"]]
    context: ContextSpace
    enable_numa_experiments: bool = False

    @model_validator(mode="after")
    def validate_lists(self) -> SearchSpaceConfig:
        named_lists: tuple[tuple[str, list[object]], ...] = (
            ("batch_sizes", list(self.batch_sizes)),
            ("ubatch_sizes", list(self.ubatch_sizes)),
            ("parallel_slots", list(self.parallel_slots)),
            ("prompt_cache", list(self.prompt_cache)),
            ("mmap", list(self.mmap)),
            ("numa_modes", list(self.numa_modes)),
        )
        for name, values in named_lists:
            if not values:
                raise ValueError(f"{name} must not be empty")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} values must be unique")
        if not any(ubatch <= batch for batch in self.batch_sizes for ubatch in self.ubatch_sizes):
            raise ValueError("search space has no valid ubatch_size <= batch_size relationship")
        return self


class SearchSpaceSource(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    path: Path
    sha256: str
    configuration: SearchSpaceConfig


class SearchPlanInput(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    source: Literal["baseline", "explicit"]
    baseline: BaselineReference | None
    hardware: HardwareFingerprint
    runtime: RuntimeFingerprint
    model: ModelFingerprint
    workload: WorkloadFingerprint
    baseline_runtime: ProfileRuntime
    baseline_peak_rss_bytes: int | None
    overrides: list[str]


class SearchPlanSummary(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    generated_profiles: int
    compatible_profiles: int
    excluded_possibilities: int
    maximum_profiles: int
    minimum_profiles: int
    thread_counts: list[int]
    batch_sizes: list[int]
    ubatch_sizes: list[int]
    parallel_slots: list[int]
    prompt_cache_values: list[bool]
    mmap_values: list[bool]
    memory_warning_profiles: int
    synthetic_fixture: bool
    candidates_executed: Literal[False] = False
    performance_conclusions_produced: Literal[False] = False


class SearchPlan(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: str
    plan_hash: str
    created_at: datetime
    goal: OptimizationGoal
    input: SearchPlanInput
    search_space: SearchSpaceSource
    candidates: list[CandidateProfile]
    excluded_possibilities: list[PlanningExclusion]
    warnings: list[PlanningWarning]
    summary: SearchPlanSummary


class PlanValidationResult(OptimizationModel):
    schema_version: Literal["1.0"] = "1.0"
    valid: bool
    plan_id: str | None
    plan_hash_valid: bool
    profile_count: int
    errors: list[str]
    warnings: list[str]
