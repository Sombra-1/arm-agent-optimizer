"""Strict models for final evidence, deployment, and bundle integrity."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class FinalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"


class BundleStatus(StrEnum):
    INITIALIZING = "initializing"
    COMPLETED = "completed"
    DIAGNOSTIC = "diagnostic"
    FAILED = "failed"


class FinalizeConfig(FinalModel):
    evaluation_dir: Path
    output_dir: Path
    allow_synthetic: bool = False
    container_image: str | None = None
    overwrite: bool = False


class ParetoRecord(FinalModel):
    candidate_id: str
    candidate_hash: str
    baseline: bool
    selected: bool
    quality_status: str
    requests_per_minute: float
    p95_latency_seconds: float
    peak_rss_bytes: int
    task_success_rate: float
    dominated: bool
    dominating_candidate_ids: list[str]


class ParetoFrontier(FinalModel):
    evaluation_id: str
    records: list[ParetoRecord]
    excluded: list[dict[str, JsonValue]]


class StageArtifactHash(FinalModel):
    stage: str
    path: str
    sha256: str


class OptimizationPassport(FinalModel):
    passport_id: str
    generated_at: datetime
    project_version: str
    outcome: str
    goal: str
    hardware: dict[str, JsonValue]
    runtime: dict[str, JsonValue]
    llama_bench: dict[str, JsonValue]
    model: dict[str, JsonValue]
    workload: dict[str, JsonValue]
    search_space_hash: str
    screening_scenario_hash: str
    quality_policy_hash: str
    baseline_summary: dict[str, JsonValue]
    screening_summary: dict[str, JsonValue]
    evaluation_summary: dict[str, JsonValue]
    drift_assessment: dict[str, JsonValue]
    selected_profile: dict[str, JsonValue] | None
    selected_command: list[str] | None
    quality_decision: dict[str, JsonValue] | None
    performance_comparison: dict[str, JsonValue] | None
    fastest_rejected_candidate: dict[str, JsonValue] | None
    pareto_frontier_reference: str
    stage_artifact_hashes: list[StageArtifactHash]
    limitations: list[str]
    reproduction_instructions: list[str]
    synthetic: bool
    hardware_specific_disclaimer: str
    selection_explanation: str
    unavailable_metrics: list[str]
    passport_content_hash: str


class PassportVerification(FinalModel):
    valid: bool
    passport_id: str | None
    content_hash_valid: bool
    errors: list[str]


class DeploymentProfile(FinalModel):
    profile_id: str
    candidate_hash: str
    goal: str
    selection_outcome: str
    runtime_settings: dict[str, JsonValue]
    runtime_binary_hash: str
    model_hash: str
    workload_hash: str
    hardware_fingerprint: dict[str, JsonValue]
    quality_policy_hash: str
    evaluation_id: str
    passport_id: str
    generated_command: list[str]
    limitations: list[str]
    scope_statement: Literal[
        "This profile is specific to the recorded hardware, runtime binary, model, "
        "workload, generation settings, and quality policy."
    ]


class ReportData(FinalModel):
    generated_at: datetime
    evaluation_id: str
    passport_id: str
    synthetic: bool
    outcome: str
    selected_candidate_id: str | None
    baseline_candidate_id: str
    hero: dict[str, JsonValue]
    funnel: dict[str, int]
    candidates: list[dict[str, JsonValue]]
    pareto: ParetoFrontier
    quality_policy: dict[str, JsonValue]
    drift: dict[str, JsonValue]
    hardware: dict[str, JsonValue]
    runtime: dict[str, JsonValue]
    model: dict[str, JsonValue]
    workload: dict[str, JsonValue]
    selected_settings: dict[str, JsonValue] | None
    fastest_rejected: dict[str, JsonValue] | None
    per_category_quality: dict[str, JsonValue]
    per_validator_quality: dict[str, JsonValue]
    reproduction_command: str
    artifact_hashes: list[StageArtifactHash]
    limitations: list[str]


class BundleManifest(FinalModel):
    bundle_id: str
    created_at: datetime
    status: BundleStatus
    evaluation_path: str
    evaluation_manifest_sha256: str
    passport_id: str
    selection_outcome: str
    selected_profile_id: str | None
    artifacts: dict[str, str]
    artifact_hashes: dict[str, str]
    validation_status: Literal["pending", "valid"]
    synthetic: bool


class FinalizeRunResult(FinalModel):
    bundle_id: str
    output_dir: Path
    status: BundleStatus
    exit_code: Literal[0, 1, 3, 4]
    outcome: str
    selected_profile_id: str | None
    passport_id: str
    synthetic: bool


class BundleValidationResult(FinalModel):
    valid: bool
    bundle_id: str | None
    errors: list[str]
    warnings: list[str]
