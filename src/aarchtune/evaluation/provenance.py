"""Validated screening consumption and current-environment provenance checks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from aarchtune.evaluation.errors import EvaluationInputError
from aarchtune.evaluation.models import (
    EvaluationCandidate,
    EvaluationConfig,
    ScreeningEvaluationReference,
)
from aarchtune.optimization.compatibility import load_explicit_input
from aarchtune.optimization.compatibility_checks import check_candidate_compatibility
from aarchtune.optimization.models import CandidateProfile, SearchPlan, SearchPlanInput
from aarchtune.runtime.capabilities import inspect_llama_server_capabilities
from aarchtune.screening.models import (
    CandidateAdvancementDecision,
    ScreeningManifest,
    ScreeningStatus,
)
from aarchtune.screening.validation import validate_screening_directory


@dataclass(frozen=True)
class LoadedEvaluationInput:
    screening_reference: ScreeningEvaluationReference
    screening_manifest: ScreeningManifest
    search_plan: SearchPlan
    current_input: SearchPlanInput
    baseline_profile: CandidateProfile
    candidates: list[EvaluationCandidate]
    warnings: list[str]
    runtime_override: bool


def _read_jsonl(path: Path, model: type[CandidateProfile]) -> list[CandidateProfile]:
    return [model.model_validate_json(line) for line in path.read_text().splitlines() if line]


def load_evaluation_input(config: EvaluationConfig) -> LoadedEvaluationInput:
    root = config.screening_dir.expanduser().resolve()
    validation = validate_screening_directory(root)
    if not validation.valid:
        raise EvaluationInputError(
            "Screening integrity validation failed: " + "; ".join(validation.errors)
        )
    manifest_path = root / "screening-manifest.json"
    manifest = ScreeningManifest.model_validate_json(manifest_path.read_text())
    if manifest.status not in {ScreeningStatus.COMPLETED, ScreeningStatus.PARTIAL}:
        raise EvaluationInputError(f"Screening status is not evaluable: {manifest.status.value}")
    if manifest.summary is None or manifest.summary.advanced_candidates < 1:
        raise EvaluationInputError("Screening advanced no candidates")
    synthetic = manifest.summary.synthetic_fixture
    if synthetic and not config.allow_synthetic:
        raise EvaluationInputError("Synthetic screening requires --allow-synthetic")
    reference = manifest.search_plan_reference
    if reference is None:
        raise EvaluationInputError("Screening manifest has no search-plan reference")
    plan = SearchPlan.model_validate_json((reference.path / "search-plan.json").read_text())
    advanced = _read_jsonl(root / "advanced-candidates.jsonl", CandidateProfile)
    if not advanced:
        raise EvaluationInputError("advanced-candidates.jsonl is empty")
    by_id = {candidate.id: candidate for candidate in advanced}
    if len(by_id) != len(advanced):
        raise EvaluationInputError("Advanced candidate IDs are duplicated")
    baseline = next((candidate for candidate in advanced if candidate.baseline), None)
    if baseline is None:
        raise EvaluationInputError("Successfully screened baseline candidate is absent")
    decisions = {
        decision.candidate_id: decision
        for line in (root / "advancement-decisions.jsonl").read_text().splitlines()
        if line
        for decision in [CandidateAdvancementDecision.model_validate_json(line)]
    }
    ordered = [candidate for candidate in plan.candidates if candidate.id in by_id]
    if len(ordered) != len(advanced):
        raise EvaluationInputError("Advanced candidates are not a subset of the search plan")
    current = load_explicit_input(
        plan.input.runtime.binary_path,
        plan.input.model.path,
        plan.input.workload.path,
    )
    warnings: list[str] = []
    if current.hardware.architecture != plan.input.hardware.architecture:
        raise EvaluationInputError("Architecture differs from screening provenance")
    hardware_fields = (
        "cpu_model",
        "physical_cores",
        "logical_cores",
        "numa_nodes",
        "total_memory_bytes",
        "features",
    )
    for field in hardware_fields:
        if getattr(current.hardware, field) != getattr(plan.input.hardware, field):
            raise EvaluationInputError(f"Hardware provenance differs: {field}")
    if current.hardware.available_memory_bytes != plan.input.hardware.available_memory_bytes:
        warnings.append("Available memory differs from the planning observation")
    runtime_changed = current.runtime.fingerprint_hash != plan.input.runtime.fingerprint_hash
    if runtime_changed and not config.allow_runtime_change:
        raise EvaluationInputError("Runtime binary, version, or capabilities changed")
    if runtime_changed:
        warnings.append("Runtime change explicitly allowed; override recorded")
    if current.model.sha256 != plan.input.model.sha256:
        raise EvaluationInputError("Model hash differs from screening provenance")
    if current.workload.sha256 != plan.input.workload.sha256:
        raise EvaluationInputError("Workload hash differs from screening provenance")
    capabilities = inspect_llama_server_capabilities(
        current.runtime.binary_path, include_probe_output=True
    )
    for candidate in ordered:
        compatibility = check_candidate_compatibility(candidate.runtime, capabilities)
        if not compatibility.compatible:
            raise EvaluationInputError(
                f"Candidate {candidate.id} cannot be represented by the current runtime: "
                + ", ".join(compatibility.unsupported_flags)
            )
    evaluation_candidates = []
    for index, candidate in enumerate(ordered, 1):
        decision = decisions.get(candidate.id)
        evaluation_candidates.append(
            EvaluationCandidate(
                order=index,
                profile=candidate,
                screening_score=decision.screening_score if decision is not None else None,
            )
        )
    screening_reference = ScreeningEvaluationReference(
        path=root,
        screening_id=manifest.screening_id,
        status=manifest.status.value,
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        goal=plan.goal,
        advanced_candidate_count=len(advanced),
        synthetic_fixture=synthetic,
    )
    return LoadedEvaluationInput(
        screening_reference=screening_reference,
        screening_manifest=manifest,
        search_plan=plan,
        current_input=current,
        baseline_profile=baseline,
        candidates=evaluation_candidates,
        warnings=warnings,
        runtime_override=runtime_changed,
    )
