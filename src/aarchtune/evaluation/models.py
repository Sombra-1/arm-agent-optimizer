"""Strict schemas for real-workload evaluation evidence and selection."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from aarchtune.benchmark.models import BaselineQualitySummary, OptionalMetric
from aarchtune.optimization.models import (
    CandidateProfile,
    HardwareFingerprint,
    ModelFingerprint,
    OptimizationGoal,
    RuntimeFingerprint,
    WorkloadFingerprint,
)
from aarchtune.workload.schema import ValidatorType


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"


class EvaluationStatus(StrEnum):
    INITIALIZING = "initializing"
    VALIDATING_SCREENING = "validating_screening"
    INSPECTING_ENVIRONMENT = "inspecting_environment"
    BUILDING_PLAN = "building_plan"
    RUNNING_BASELINE_START = "running_baseline_start"
    RUNNING_CANDIDATES = "running_candidates"
    RUNNING_BASELINE_END = "running_baseline_end"
    ASSESSING_DRIFT = "assessing_drift"
    APPLYING_QUALITY_POLICY = "applying_quality_policy"
    RANKING = "ranking"
    SELECTING = "selecting"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class CandidateExecutionStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNSUPPORTED = "unsupported"
    INTERRUPTED = "interrupted"


class QualityGateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    INCOMPARABLE = "incomparable"


class DriftClassification(StrEnum):
    STABLE = "stable"
    WARNING = "warning"
    INVALIDATING = "invalidating"
    UNAVAILABLE = "unavailable"


class SelectionOutcome(StrEnum):
    CANDIDATE_SELECTED = "candidate_selected"
    BASELINE_RETAINED = "baseline_retained"
    NO_ELIGIBLE_CANDIDATE = "no_eligible_candidate"
    INVALIDATED_BY_DRIFT = "evaluation_invalidated_by_drift"
    EVALUATION_FAILED = "evaluation_failed"


class RateThresholds(EvaluationModel):
    request_success_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    task_success_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    json_validity_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    validator_pass_rate: Annotated[float, Field(ge=0.0, le=1.0)]


class QualityMaximums(EvaluationModel):
    timeout_rate: Annotated[float, Field(ge=0.0, le=1.0)]


class MinimumEvidence(EvaluationModel):
    completed_attempt_fraction: Annotated[float, Field(gt=0.0, le=1.0)]
    repetitions_per_task: Annotated[int, Field(ge=1, le=100)]


class PerformanceDriftThresholds(EvaluationModel):
    median_latency_relative_change: Annotated[float, Field(ge=0.0, le=2.0)]
    p95_latency_relative_change: Annotated[float, Field(ge=0.0, le=2.0)]
    requests_per_minute_relative_change: Annotated[float, Field(ge=0.0, le=2.0)]
    generation_throughput_relative_change: Annotated[float, Field(ge=0.0, le=2.0)]


class QualityDriftThresholds(EvaluationModel):
    task_success_absolute_change: Annotated[float, Field(ge=0.0, le=1.0)]
    json_validity_absolute_change: Annotated[float, Field(ge=0.0, le=1.0)]
    validator_pass_absolute_change: Annotated[float, Field(ge=0.0, le=1.0)]


class DriftPolicy(EvaluationModel):
    performance: PerformanceDriftThresholds
    quality: QualityDriftThresholds


class BalancedWeights(EvaluationModel):
    requests_per_minute: Annotated[float, Field(ge=0.0, le=1.0)] = 0.35
    inverse_p95_latency: Annotated[float, Field(ge=0.0, le=1.0)] = 0.25
    inverse_peak_rss: Annotated[float, Field(ge=0.0, le=1.0)] = 0.20
    consistency: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10
    quality_margin: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10

    @model_validator(mode="after")
    def positive_total(self) -> BalancedWeights:
        if sum(self.model_dump(exclude={"schema_version"}).values()) <= 0:
            raise ValueError("balanced weights must have a positive sum")
        return self


class MinimumSelectionImprovement(EvaluationModel):
    latency_relative: Annotated[float, Field(ge=0.0, le=1.0)] = 0.02
    throughput_relative: Annotated[float, Field(ge=0.0, le=1.0)] = 0.02
    memory_relative: Annotated[float, Field(ge=0.0, le=1.0)] = 0.02
    balanced_score_absolute: Annotated[float, Field(ge=0.0, le=1.0)] = 0.01


class QualityPolicy(EvaluationModel):
    absolute_minimums: RateThresholds
    maximum_regression_from_baseline: RateThresholds
    maximums: QualityMaximums
    minimum_evidence: MinimumEvidence
    critical_validator_types: list[ValidatorType]
    drift: DriftPolicy
    balanced_weights: BalancedWeights = Field(default_factory=BalancedWeights)
    minimum_selection_improvement: MinimumSelectionImprovement = Field(
        default_factory=MinimumSelectionImprovement
    )

    @model_validator(mode="after")
    def unique_critical_validators(self) -> QualityPolicy:
        if len(self.critical_validator_types) != len(set(self.critical_validator_types)):
            raise ValueError("critical_validator_types must be unique")
        return self


class QualityPolicySource(EvaluationModel):
    path: Path
    sha256: str
    policy: QualityPolicy


class EvaluationConfig(EvaluationModel):
    screening_dir: Path
    output_dir: Path
    repetitions: Annotated[int, Field(ge=1, le=100)] = 3
    warmup_requests: Annotated[int, Field(ge=0, le=100)] = 1
    quality_policy_path: Path | None = None
    request_timeout_seconds: Annotated[float, Field(ge=0.1, le=600.0)] = 60.0
    startup_timeout_seconds: Annotated[float, Field(ge=0.1, le=600.0)] = 30.0
    shutdown_timeout_seconds: Annotated[float, Field(ge=0.1, le=60.0)] = 5.0
    sample_interval_seconds: Annotated[float, Field(ge=0.05, le=5.0)] = 0.1
    settling_delay_seconds: Annotated[float, Field(ge=0.0, le=60.0)] = 0.0
    maximum_candidate_failures: Annotated[int, Field(ge=1, le=100)] = 3
    maximum_total_duration_seconds: Annotated[float, Field(ge=1.0, le=86_400.0)] = 7200.0
    allow_synthetic: bool = False
    allow_runtime_change: bool = False
    overwrite: bool = False


class ScreeningEvaluationReference(EvaluationModel):
    path: Path
    screening_id: str
    status: str
    manifest_sha256: str
    plan_id: str
    plan_hash: str
    goal: OptimizationGoal
    advanced_candidate_count: int
    synthetic_fixture: bool


class EvaluationCandidate(EvaluationModel):
    order: int
    profile: CandidateProfile
    screening_score: float | None


class EvaluationPlan(EvaluationModel):
    evaluation_id: str
    plan_hash: str
    goal: OptimizationGoal
    baseline_start_profile: CandidateProfile
    candidates: list[EvaluationCandidate]
    baseline_end_profile: CandidateProfile
    execution_order: list[str]
    task_order: list[str]
    warmup_requests: int
    repetitions: int
    request_timeout_seconds: float
    startup_timeout_seconds: float
    sample_interval_seconds: float
    settling_delay_seconds: float
    quality_policy_sha256: str
    expected_attempt_count: int
    maximum_candidate_failures: int
    maximum_total_duration_seconds: float
    deterministic_ordering_bias: str


class CandidateConsistency(EvaluationModel):
    tasks_passing_every_repetition: int
    tasks_failing_at_least_once: int
    inconsistent_task_count: int
    per_task_pass_consistency_rate: float | None
    latency_coefficient_of_variation: float | None
    throughput_coefficient_of_variation: float | None


class CandidatePerformanceSummary(EvaluationModel):
    configured_attempts: int
    completed_attempts: int
    successful_requests: int
    request_success_rate: float | None
    median_latency_seconds: float | None
    p95_latency_seconds: float | None
    mean_latency_seconds: float | None
    requests_per_minute: float | None
    prompt_tokens_total: int
    completion_tokens_total: int
    server_prompt_throughput: float | None
    server_generation_throughput: float | None
    measured_peak_rss_bytes: int | None
    whole_run_peak_rss_bytes: int | None
    mean_measured_rss_bytes: float | None
    mean_cpu_percent: float | None
    peak_cpu_percent: float | None
    time_to_first_token: OptionalMetric
    comparable: bool
    comparability_reasons: list[str]


class CandidateQualitySummary(EvaluationModel):
    aggregate: BaselineQualitySummary
    consistency: CandidateConsistency


class CandidateExecutionResult(EvaluationModel):
    candidate_id: str
    candidate_hash: str
    label: str
    profile: CandidateProfile
    status: CandidateExecutionStatus
    run_id: str
    run_directory: Path
    screening_score: float | None
    performance: CandidatePerformanceSummary | None
    quality: CandidateQualitySummary | None
    failure_type: str | None = None
    failure_message: str | None = None
    server_stopped: bool
    sampler_stopped: bool


class QualityMetricDecision(EvaluationModel):
    metric: str
    baseline_value: float | None
    candidate_value: float | None
    absolute_threshold: float | None
    regression_limit: float | None
    observed_regression: float | None
    passed: bool
    reason: str


class CriticalValidatorDecision(EvaluationModel):
    validator_type: ValidatorType
    baseline_failures: int
    candidate_failures: int
    baseline_failure_rate: float | None
    candidate_failure_rate: float | None
    passed: bool
    inherited_baseline_limitation: bool
    reason: str


class QualityViolation(EvaluationModel):
    code: str
    metric: str | None
    reason: str


class QualityDecision(EvaluationModel):
    candidate_id: str
    candidate_hash: str
    status: QualityGateStatus
    metric_decisions: list[QualityMetricDecision]
    critical_validator_decisions: list[CriticalValidatorDecision]
    violations: list[QualityViolation]
    completed_attempt_fraction: float | None
    observed_repetitions_per_task: int | None


class MetricImprovement(EvaluationModel):
    metric: str
    baseline_value: float | None
    candidate_value: float | None
    improvement: float | None
    available: bool
    higher_is_better: bool
    reason: str | None = None


class CandidateComparison(EvaluationModel):
    candidate_id: str
    candidate_hash: str
    comparable: bool
    reasons: list[str]
    improvements: list[MetricImprovement]


class DriftMetric(EvaluationModel):
    metric: str
    start_value: float | None
    end_value: float | None
    observed_change: float | None
    threshold: float
    absolute_change: bool
    classification: DriftClassification
    reason: str


class DriftAssessment(EvaluationModel):
    classification: DriftClassification
    metrics: list[DriftMetric]
    reasons: list[str]


class RankingComponent(EvaluationModel):
    component: str
    raw_value: float | None
    normalized_value: float | None
    weight: float
    contribution: float | None
    available: bool
    reason: str | None = None


class CandidateRankingResult(EvaluationModel):
    candidate_id: str
    candidate_hash: str
    position: int
    score: float | None
    components: list[RankingComponent]
    tie_break_values: list[JsonValue]


class SelectionDecision(EvaluationModel):
    selection_id: str
    outcome: SelectionOutcome
    selected_candidate_id: str | None
    selected_candidate_hash: str | None
    baseline_candidate_id: str
    goal: OptimizationGoal
    ranking_position: int | None
    applicable_improvement: float | None
    practical_improvement_threshold: float | None
    reason_code: str
    reason: str
    screening_reference: ScreeningEvaluationReference


class EvaluationSummary(EvaluationModel):
    evaluation_id: str
    status: EvaluationStatus
    goal: OptimizationGoal
    advanced_candidates: int
    candidates_completed: int
    candidates_failed: int
    quality_passed: int
    quality_rejected: int
    drift: DriftClassification
    selection: SelectionOutcome
    selected_candidate_id: str | None
    synthetic_fixture: bool


class EvaluationManifest(EvaluationModel):
    evaluation_id: str
    created_at: datetime
    updated_at: datetime
    status: EvaluationStatus
    stage: EvaluationStatus
    output_directory: Path
    configuration: EvaluationConfig
    screening_reference: ScreeningEvaluationReference | None = None
    hardware_fingerprint: HardwareFingerprint | None = None
    runtime_fingerprint: RuntimeFingerprint | None = None
    model_fingerprint: ModelFingerprint | None = None
    workload_fingerprint: WorkloadFingerprint | None = None
    provenance_warnings: list[str] = Field(default_factory=list)
    runtime_change_override_applied: bool = False
    execution_plan_hash: str | None = None
    completed_executions: int = 0
    failed_executions: int = 0
    quality_decisions: int = 0
    ranked_candidates: int = 0
    selected_candidate_id: str | None = None
    owned_processes_stopped: bool | None = None
    samplers_stopped: bool | None = None
    summary: EvaluationSummary | None = None
    error_type: str | None = None
    error_message: str | None = None


class SelectedProfile(EvaluationModel):
    selection_id: str
    evaluation_id: str
    candidate_id: str
    candidate_hash: str
    hardware_fingerprint: HardwareFingerprint
    runtime_binary_hash: str
    model_hash: str
    workload_hash: str
    quality_policy_hash: str
    goal: OptimizationGoal
    runtime_configuration: dict[str, JsonValue]
    performance_summary: CandidatePerformanceSummary
    quality_summary: CandidateQualitySummary
    baseline_comparison: CandidateComparison
    limitations: list[str]
    scope_statement: Literal[
        "This profile is specific to the recorded hardware, runtime binary, model, "
        "workload, and evaluation settings."
    ]


class EvaluationRunResult(EvaluationModel):
    evaluation_id: str
    output_dir: Path
    status: EvaluationStatus
    exit_code: Literal[0, 2, 3, 4]
    summary: EvaluationSummary | None = None
    selection: SelectionDecision | None = None


class EvaluationValidationResult(EvaluationModel):
    valid: bool
    evaluation_id: str | None
    errors: list[str]
    warnings: list[str]
