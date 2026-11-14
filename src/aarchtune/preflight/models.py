"""Response-free preflight reports with explicit policy and evidence decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PreflightModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PolicyCheck(PreflightModel):
    name: str
    observed: float | None
    threshold: float
    comparison: Literal["minimum", "maximum"]
    passed: bool


class TaskDiagnosis(PreflightModel):
    task_id: str
    attempts: int
    passed: int
    failures: dict[str, int]


class PreflightReport(PreflightModel):
    schema_version: Literal["1.0"] = "1.0"
    baseline_dir: Path
    baseline_run_id: str | None = None
    policy_sha256: str
    evidence_sha256: dict[str, str] = Field(default_factory=dict)
    outcome: Literal["quality_passed", "quality_failed", "evidence_incomplete"]
    can_tune: bool
    synthetic: bool = False
    is_arm64: bool = False
    response_format: str | None = None
    checks: list[PolicyCheck]
    evidence_errors: list[str]
    tasks: list[TaskDiagnosis]
    recommendations: list[str]
    deployment_recommendation: str = "Retain current configuration; no verified improvement."
