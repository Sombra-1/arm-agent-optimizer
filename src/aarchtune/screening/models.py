"""Strict persisted models for low-level screening evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from aarchtune.benchmark.models import NumericStatistics, ProcessMetricsSummary
from aarchtune.optimization.models import CandidateProfile, OptimizationGoal, SearchPlan
from aarchtune.runtime.capabilities import ProbeResult


class ScreeningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"


class OutputFormat(StrEnum):
    JSONL = "jsonl"
    JSON = "json"
    CSV = "csv"


class BooleanOptionForm(StrEnum):
    NUMERIC_01 = "numeric_01"
    PAIRED_SWITCHES = "paired_switches"
    TRUE_ONLY = "true_only"
    UNSUPPORTED = "unsupported"


class ScreeningStatus(StrEnum):
    INITIALIZING = "initializing"
    VALIDATING_PLAN = "validating_plan"
    INSPECTING_BENCH = "inspecting_bench"
    BUILDING_MATRIX = "building_matrix"
    EXECUTING = "executing"
    NORMALIZING = "normalizing"
    SELECTING = "selecting"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class SignatureStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    TIMED_OUT = "timed_out"
    UNSTABLE = "unstable"


class StabilityClass(StrEnum):
    STABLE = "stable"
    VARIABLE = "variable"
    HIGHLY_VARIABLE = "highly_variable"
    INSUFFICIENT_DATA = "insufficient_data"


class DecisionStatus(StrEnum):
    ADVANCED = "advanced"
    NOT_ADVANCED = "not_advanced"
    EXCLUDED = "excluded"
    UNSCREENABLE = "unscreenable"
    SCREENING_FAILED = "screening_failed"


class MetricKind(StrEnum):
    PREFILL = "prefill"
    DECODE = "decode"
    COMBINED = "combined"


class CapabilityMapping(ScreeningModel):
    logical_parameter: str
    supported: bool
    selected_flag: str | None
    aliases_observed: list[str]
    boolean_form: BooleanOptionForm | None = None

    def represents_boolean(self, value: bool) -> bool:
        if self.boolean_form in {
            BooleanOptionForm.NUMERIC_01,
            BooleanOptionForm.PAIRED_SWITCHES,
        }:
            return True
        return value and self.boolean_form is BooleanOptionForm.TRUE_ONLY


class OutputFormatSelection(ScreeningModel):
    requested_format: OutputFormat | None
    selected_format: OutputFormat
    supported_formats: list[OutputFormat]
    selection_reason: str


class LlamaBenchCapabilities(ScreeningModel):
    binary_path: Path
    binary_sha256: str
    binary_size: int
    binary_mtime_ns: int
    version: str | None
    raw_option_tokens: list[str]
    mappings: dict[str, CapabilityMapping]
    output: OutputFormatSelection
    version_probe: ProbeResult
    help_probe: ProbeResult
    synthetic_fixture: bool = False


class ScreeningConfig(ScreeningModel):
    plan_dir: Path
    bench_binary: Path | None
    output_dir: Path
    scenario_path: Path | None = None
    advance_count: Annotated[int, Field(ge=1, le=24)] = 6
    repetitions: Annotated[int, Field(ge=1, le=20)] = 3
    maximum_unique_signatures: Annotated[int, Field(ge=1, le=64)] = 24
    maximum_scenarios: Annotated[int, Field(ge=1, le=8)] = 4
    maximum_invocations: Annotated[int, Field(ge=1, le=512)] = 288
    invocation_timeout_seconds: Annotated[float, Field(ge=0.1, le=3600.0)] = 120.0
    total_timeout_seconds: Annotated[float, Field(ge=1.0, le=86_400.0)] = 3600.0
    shutdown_timeout_seconds: Annotated[float, Field(ge=0.1, le=60.0)] = 3.0
    sample_interval_seconds: Annotated[float, Field(ge=0.05, le=5.0)] = 0.1
    maximum_log_bytes: Annotated[int, Field(ge=4096, le=16 * 1024 * 1024)] = 1024 * 1024
    stable_cv_maximum: Annotated[float, Field(ge=0.0, le=1.0)] = 0.03
    variable_cv_maximum: Annotated[float, Field(gt=0.0, le=2.0)] = 0.10
    allow_synthetic: bool = False
    overwrite: bool = False

    @model_validator(mode="after")
    def validate_thresholds(self) -> ScreeningConfig:
        if self.stable_cv_maximum > self.variable_cv_maximum:
            raise ValueError("stable CV threshold cannot exceed variable CV threshold")
        return self


class ScreeningScenario(ScreeningModel):
    id: str
    prompt_tokens: Annotated[int, Field(ge=0, le=32768)]
    generation_tokens: Annotated[int, Field(ge=0, le=32768)]
    required: bool = True

    @model_validator(mode="after")
    def nonempty(self) -> ScreeningScenario:
        if self.prompt_tokens == 0 and self.generation_tokens == 0:
            raise ValueError("a scenario must request prompt or generation tokens")
        if not self.id or len(self.id) > 64:
            raise ValueError("scenario ID must contain 1-64 characters")
        return self

    @property
    def metric_kind(self) -> MetricKind:
        if self.prompt_tokens and self.generation_tokens:
            return MetricKind.COMBINED
        return MetricKind.PREFILL if self.prompt_tokens else MetricKind.DECODE


class ScenarioSet(ScreeningModel):
    scenarios: list[ScreeningScenario]

    @model_validator(mode="after")
    def unique_ids(self) -> ScenarioSet:
        if not self.scenarios or len(self.scenarios) > 8:
            raise ValueError("scenario count must be between 1 and 8")
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario IDs must be unique")
        return self


class ScenarioSource(ScreeningModel):
    path: Path
    sha256: str
    scenarios: list[ScreeningScenario]
    omitted_scenarios: list[dict[str, JsonValue]] = Field(default_factory=list)


class CandidateFieldMapping(ScreeningModel):
    field: str
    screenable: bool
    value: JsonValue
    reason: str


class BenchSignatureSettings(ScreeningModel):
    threads: int | None = None
    threads_batch: int | None = None
    batch_size: int | None = None
    ubatch_size: int | None = None
    mmap: bool | None = None
    numa_mode: str | None = None


class BenchSignature(ScreeningModel):
    id: str
    signature_hash: str
    settings: BenchSignatureSettings
    compatible: bool
    incompatibility_reasons: list[str] = Field(default_factory=list)


class SignatureMembership(ScreeningModel):
    candidate_id: str
    candidate_hash: str
    bench_signature_id: str
    bench_signature_hash: str
    screenable_fields: list[CandidateFieldMapping]
    unscreenable_fields: list[CandidateFieldMapping]


class BenchCommand(ScreeningModel):
    arguments: list[str]
    mapped_flags: dict[str, str]
    output_format: OutputFormat
    signature_id: str
    scenario_id: str
    repetition: int


class MatrixEntry(ScreeningModel):
    invocation_id: str
    signature_id: str
    signature_hash: str
    scenario_id: str
    repetition: int
    command: BenchCommand


class BenchExecutionResult(ScreeningModel):
    invocation_id: str
    command: BenchCommand
    pid: int | None
    started_at: datetime
    finished_at: datetime
    elapsed_ns: int
    exit_code: int | None
    timed_out: bool
    interrupted: bool
    forced_termination: bool
    stdout_path: str
    stderr_path: str
    process_samples_path: str
    stdout_bytes: int
    stderr_bytes: int
    stderr_truncated: bool
    process_summary: ProcessMetricsSummary
    sampler_stopped: bool
    process_stopped: bool


class RawBenchRecord(ScreeningModel):
    invocation_id: str
    row_index: int
    raw: dict[str, JsonValue]


class CanonicalValue(ScreeningModel):
    available: bool
    value: int | float | str | None
    source_path: str | None = None
    reason: str | None = None


class NormalizedBenchMeasurement(ScreeningModel):
    measurement_id: str
    invocation_id: str
    row_index: int
    scenario_id: str
    signature_id: str
    metric_kind: MetricKind
    prompt_tokens: CanonicalValue
    generation_tokens: CanonicalValue
    threads: CanonicalValue
    threads_batch: CanonicalValue
    batch_size: CanonicalValue
    ubatch_size: CanonicalValue
    throughput_tokens_per_second: CanonicalValue
    throughput_standard_deviation: CanonicalValue
    test_time_seconds: CanonicalValue
    model_size_bytes: CanonicalValue
    model_parameter_count: CanonicalValue
    backend: CanonicalValue
    build_commit: CanonicalValue
    build_number: CanonicalValue
    provenance_valid: bool
    provenance_errors: list[str] = Field(default_factory=list)


class StabilityAssessment(ScreeningModel):
    measurement_count: int
    failed_repetition_count: int
    timeout_count: int
    coefficient_of_variation: float | None
    classification: StabilityClass


class ScenarioAggregate(ScreeningModel):
    signature_id: str
    scenario_id: str
    metric_kind: MetricKind
    throughput: NumericStatistics
    stability: StabilityAssessment
    successful_repetitions: int
    failed_repetitions: int


class ScoreComponent(ScreeningModel):
    component: str
    raw_value: float | None
    normalized_value: float | None
    weight: float
    contribution: float | None
    available: bool
    reason: str | None = None


class SignatureScreeningResult(ScreeningModel):
    signature_id: str
    signature_hash: str
    status: SignatureStatus
    supported_scenarios: list[str]
    successful_scenarios: list[str]
    failed_scenarios: list[str]
    scenario_aggregates: list[ScenarioAggregate]
    process_peak_rss_bytes: int | None
    stability: StabilityAssessment
    screening_eligible: bool
    reasons: list[str]
    member_candidate_ids: list[str]
    score: float | None = None
    score_components: list[ScoreComponent] = Field(default_factory=list)


class CandidateAdvancementDecision(ScreeningModel):
    candidate_id: str
    candidate_hash: str
    signature_id: str
    decision: DecisionStatus
    reason_code: str
    reason: str
    screening_score: float | None


class SearchPlanReference(ScreeningModel):
    path: Path
    plan_id: str
    plan_hash: str
    goal: OptimizationGoal
    candidate_count: int
    synthetic_fixture: bool


class LlamaBenchFingerprint(ScreeningModel):
    path: Path
    sha256: str
    size_bytes: int
    modification_time_ns: int
    version: str | None
    synthetic_fixture: bool


class ScreeningSummary(ScreeningModel):
    screening_id: str
    status: ScreeningStatus
    plan_profiles: int
    bench_signatures: int
    scenarios: int
    expected_invocations: int
    completed_invocations: int
    failed_invocations: int
    successful_signatures: int
    partial_signatures: int
    failed_signatures: int
    advanced_candidates: int
    synthetic_fixture: bool
    quality_evaluated: Literal[False] = False
    final_candidate_selected: Literal[False] = False


class ScreeningManifest(ScreeningModel):
    screening_id: str
    created_at: datetime
    updated_at: datetime
    status: ScreeningStatus
    stage: ScreeningStatus
    output_directory: Path
    search_plan_reference: SearchPlanReference | None = None
    hardware_fingerprint: dict[str, JsonValue] | None = None
    model_fingerprint: dict[str, JsonValue] | None = None
    llama_bench_fingerprint: LlamaBenchFingerprint | None = None
    screening_configuration: ScreeningConfig
    scenarios: list[ScreeningScenario] = Field(default_factory=list)
    signature_membership: list[SignatureMembership] = Field(default_factory=list)
    raw_result_references: list[str] = Field(default_factory=list)
    normalized_results: int = 0
    failed_signatures: list[str] = Field(default_factory=list)
    advancement_decisions: int = 0
    summary: ScreeningSummary | None = None
    completed_invocations: int = 0
    failed_invocations: int = 0
    advanced_candidate_count: int = 0
    owned_processes_stopped: bool | None = None
    samplers_stopped: bool | None = None
    error_type: str | None = None
    error_message: str | None = None


class ScreeningRunResult(ScreeningModel):
    screening_id: str
    output_dir: Path
    status: ScreeningStatus
    exit_code: Literal[0, 2, 3]
    summary: ScreeningSummary | None


class ScreeningValidationResult(ScreeningModel):
    valid: bool
    screening_id: str | None
    errors: list[str]
    warnings: list[str]


class LoadedScreeningInput(ScreeningModel):
    plan: SearchPlan
    candidates: list[CandidateProfile]
