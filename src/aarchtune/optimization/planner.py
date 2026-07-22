"""Pure plan assembly from validated provenance and a bounded search space."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aarchtune.optimization.artifacts import write_plan_artifacts
from aarchtune.optimization.compatibility import load_baseline_input, load_explicit_input
from aarchtune.optimization.errors import PlanningError
from aarchtune.optimization.generator import generate_candidates
from aarchtune.optimization.identity import plan_hash, stable_hash
from aarchtune.optimization.models import (
    MemoryRiskClass,
    OptimizationGoal,
    SearchPlan,
    SearchPlanSummary,
)
from aarchtune.optimization.search_space import load_search_space
from aarchtune.runtime.capabilities import inspect_llama_server_capabilities


def create_search_plan(
    *,
    goal: OptimizationGoal,
    output_dir: Path,
    baseline_dir: Path | None = None,
    binary: Path | None = None,
    model: Path | None = None,
    workload: Path | None = None,
    search_space_path: Path | None = None,
    maximum_profiles: int | None = None,
    allow_synthetic: bool = False,
    allow_runtime_change: bool = False,
    overwrite: bool = False,
) -> tuple[SearchPlan, Path]:
    if baseline_dir is not None:
        if any(value is not None for value in (binary, model, workload)):
            raise PlanningError(
                "Use either --baseline or explicit --binary/--model/--workload inputs"
            )
        plan_input = load_baseline_input(
            baseline_dir,
            allow_synthetic=allow_synthetic,
            allow_runtime_change=allow_runtime_change,
        )
    else:
        if binary is None or model is None or workload is None:
            raise PlanningError(
                "Explicit planning requires --binary, --model, and --workload together"
            )
        plan_input = load_explicit_input(binary, model, workload)
    search_source = load_search_space(search_space_path)
    configured_maximum = search_source.configuration.limits.maximum_profiles
    limit = maximum_profiles if maximum_profiles is not None else configured_maximum
    if limit < 1 or limit > configured_maximum:
        raise PlanningError(
            f"--max-profiles must be between 1 and configured maximum {configured_maximum}"
        )
    capabilities = inspect_llama_server_capabilities(
        plan_input.runtime.binary_path, include_probe_output=True
    )
    generation = generate_candidates(
        plan_input,
        capabilities,
        search_source.configuration,
        goal,
        limit,
    )
    candidates = generation.candidates
    summary = SearchPlanSummary(
        generated_profiles=generation.unique_generated,
        compatible_profiles=len(candidates),
        excluded_possibilities=len(generation.exclusions),
        maximum_profiles=limit,
        minimum_profiles=search_source.configuration.limits.minimum_profiles,
        thread_counts=sorted(
            {value for candidate in candidates if (value := candidate.runtime.threads) is not None}
        ),
        batch_sizes=sorted(
            {
                value
                for candidate in candidates
                if (value := candidate.runtime.batch_size) is not None
            }
        ),
        ubatch_sizes=sorted(
            {
                value
                for candidate in candidates
                if (value := candidate.runtime.ubatch_size) is not None
            }
        ),
        parallel_slots=sorted(
            {
                value
                for candidate in candidates
                if (value := candidate.runtime.parallel_slots) is not None
            }
        ),
        prompt_cache_values=sorted({candidate.runtime.prompt_cache for candidate in candidates}),
        mmap_values=sorted({candidate.runtime.mmap for candidate in candidates}),
        memory_warning_profiles=sum(
            candidate.resource_estimate.classification
            in {MemoryRiskClass.WARNING, MemoryRiskClass.HIGH_RISK}
            for candidate in candidates
        ),
        synthetic_fixture=(
            plan_input.model.synthetic_fixture
            or (plan_input.baseline.synthetic_fixture if plan_input.baseline else False)
        ),
    )
    semantic_seed = {
        "goal": goal.value,
        "input": plan_input.model_dump(mode="json"),
        "search_space_sha256": search_source.sha256,
        "maximum_profiles": limit,
        "candidate_hashes": [candidate.profile_hash for candidate in candidates],
        "exclusions": [item.model_dump(mode="json") for item in generation.exclusions],
        "warnings": [item.model_dump(mode="json") for item in generation.warnings],
    }
    plan_id = f"plan-{goal.value}-{stable_hash(semantic_seed)[:12]}"
    plan = SearchPlan(
        plan_id=plan_id,
        plan_hash="pending",
        created_at=datetime.now(UTC),
        goal=goal,
        input=plan_input,
        search_space=search_source,
        candidates=candidates,
        excluded_possibilities=generation.exclusions,
        warnings=generation.warnings,
        summary=summary,
    )
    plan = plan.model_copy(update={"plan_hash": plan_hash(plan)})
    root = write_plan_artifacts(plan, output_dir, overwrite=overwrite)
    return plan, root
