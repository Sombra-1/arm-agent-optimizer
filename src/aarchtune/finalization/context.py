"""Validated evaluation loading shared by final artifact generators."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

from aarchtune.evaluation.models import (
    CandidateComparison,
    CandidateExecutionResult,
    DriftAssessment,
    EvaluationManifest,
    EvaluationPlan,
    EvaluationSummary,
    QualityDecision,
    QualityPolicySource,
    SelectedProfile,
    SelectionDecision,
    SelectionOutcome,
)
from aarchtune.evaluation.validation import validate_evaluation_directory
from aarchtune.finalization.errors import FinalizationInputError
from aarchtune.optimization.models import SearchPlan
from aarchtune.screening.models import ScreeningManifest, ScreeningSummary

ModelT = TypeVar("ModelT", bound=BaseModel)


def load_model(path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    return [model.model_validate_json(line) for line in path.read_text().splitlines() if line]


@dataclass(frozen=True)
class FinalizationContext:
    root: Path
    manifest: EvaluationManifest
    summary: EvaluationSummary
    plan: EvaluationPlan
    policy: QualityPolicySource
    selection: SelectionDecision
    drift: DriftAssessment
    results: list[CandidateExecutionResult]
    decisions: list[QualityDecision]
    comparisons: list[CandidateComparison]
    selected_profile: SelectedProfile | None
    screening_root: Path
    screening_manifest: ScreeningManifest
    screening_summary: ScreeningSummary
    search_plan_root: Path
    search_plan: SearchPlan
    baseline_root: Path | None


def load_finalization_context(
    evaluation_dir: Path, *, allow_synthetic: bool
) -> FinalizationContext:
    root = evaluation_dir.expanduser().resolve()
    validation = validate_evaluation_directory(root)
    if not validation.valid:
        raise FinalizationInputError(
            "Evaluation integrity validation failed: " + "; ".join(validation.errors)
        )
    manifest = load_model(root / "evaluation-manifest.json", EvaluationManifest)
    summary = load_model(root / "evaluation-summary.json", EvaluationSummary)
    if summary.synthetic_fixture and not allow_synthetic:
        raise FinalizationInputError("Synthetic evaluation requires --allow-synthetic")
    selection = load_model(root / "selection.json", SelectionDecision)
    if selection.outcome is SelectionOutcome.EVALUATION_FAILED:
        raise FinalizationInputError("Failed evaluation cannot be finalized")
    selected_path = root / "selected-profile.yaml"
    selected = None
    if selected_path.is_file():
        selected = SelectedProfile.model_validate_json(
            json.dumps(yaml.safe_load(selected_path.read_text(encoding="utf-8")))
        )
    screening_root = selection.screening_reference.path.resolve()
    screening_manifest = load_model(screening_root / "screening-manifest.json", ScreeningManifest)
    if screening_manifest.summary is None or screening_manifest.search_plan_reference is None:
        raise FinalizationInputError("Screening evidence lacks final summary or plan reference")
    search_plan_root = screening_manifest.search_plan_reference.path.resolve()
    search_plan = load_model(search_plan_root / "search-plan.json", SearchPlan)
    baseline_root = (
        search_plan.input.baseline.path.resolve() if search_plan.input.baseline else None
    )
    return FinalizationContext(
        root=root,
        manifest=manifest,
        summary=summary,
        plan=load_model(root / "execution-plan.json", EvaluationPlan),
        policy=load_model(root / "quality-policy.json", QualityPolicySource),
        selection=selection,
        drift=load_model(root / "drift-assessment.json", DriftAssessment),
        results=load_jsonl(root / "candidate-results.jsonl", CandidateExecutionResult),
        decisions=load_jsonl(root / "quality-decisions.jsonl", QualityDecision),
        comparisons=load_jsonl(root / "candidate-comparisons.jsonl", CandidateComparison),
        selected_profile=selected,
        screening_root=screening_root,
        screening_manifest=screening_manifest,
        screening_summary=screening_manifest.summary,
        search_plan_root=search_plan_root,
        search_plan=search_plan,
        baseline_root=baseline_root,
    )
