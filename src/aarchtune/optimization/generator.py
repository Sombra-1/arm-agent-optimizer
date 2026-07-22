"""Small staged candidate generator; deliberately never forms a Cartesian product."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aarchtune.optimization.compatibility_checks import check_candidate_compatibility
from aarchtune.optimization.errors import PlanningError
from aarchtune.optimization.goals import GOAL_PREFERENCES
from aarchtune.optimization.identity import profile_hash, profile_id
from aarchtune.optimization.memory import assess_memory_risk
from aarchtune.optimization.models import (
    CandidateParameterSource,
    CandidateProfile,
    MemoryRiskClass,
    OptimizationGoal,
    ParameterSourceKind,
    PlanningExclusion,
    PlanningWarning,
    ProfileRuntime,
    SearchPlanInput,
    SearchSpaceConfig,
)
from aarchtune.runtime.capabilities import ServerCapabilities


@dataclass(frozen=True)
class Draft:
    label: str
    stage: str
    runtime: ProfileRuntime
    goal_tags: tuple[OptimizationGoal, ...]
    sources: dict[str, CandidateParameterSource]
    rationale: tuple[str, ...]
    baseline: bool = False


@dataclass(frozen=True)
class GenerationResult:
    candidates: list[CandidateProfile]
    exclusions: list[PlanningExclusion]
    warnings: list[PlanningWarning]
    unique_generated: int


def _source(kind: ParameterSourceKind, detail: str) -> CandidateParameterSource:
    return CandidateParameterSource(source=kind, detail=detail)


def _base_sources(runtime: ProfileRuntime) -> dict[str, CandidateParameterSource]:
    return {
        field: _source(
            ParameterSourceKind.BASELINE, "Preserved from baseline or explicit runtime default"
        )
        for field in ProfileRuntime.model_fields
    }


def generate_thread_values(
    plan_input: SearchPlanInput, search: SearchSpaceConfig
) -> dict[int, CandidateParameterSource]:
    hardware = plan_input.hardware
    core_basis = hardware.physical_cores or hardware.logical_cores
    if core_basis is None or core_basis < 1:
        core_basis = 1
    source_kind = (
        ParameterSourceKind.FRACTION_PHYSICAL
        if hardware.physical_cores is not None
        else ParameterSourceKind.FRACTION_LOGICAL
    )
    label = "physical" if hardware.physical_cores is not None else "logical"
    logical_limit = hardware.logical_cores or core_basis
    values: dict[int, CandidateParameterSource] = {}
    for fraction in search.threads.fractions_of_physical_cores:
        value = max(1, min(logical_limit, int(core_basis * fraction + 0.5)))
        values[value] = _source(source_kind, f"{fraction:.2f} of {core_basis} {label} cores")
    baseline_threads = plan_input.baseline_runtime.threads
    if (
        search.threads.include_baseline
        and baseline_threads is not None
        and baseline_threads <= logical_limit
    ):
        values[baseline_threads] = _source(
            ParameterSourceKind.BASELINE, "Exact baseline generation thread count"
        )
    values.setdefault(
        1, _source(ParameterSourceKind.GOAL_SPECIFIC, "Conservative one-thread bound")
    )
    values.setdefault(
        min(core_basis, logical_limit),
        _source(ParameterSourceKind.GOAL_SPECIFIC, "Full available core-basis count"),
    )
    return dict(sorted(values.items()))


def _thread_batch_values(
    plan_input: SearchPlanInput, search: SearchSpaceConfig
) -> dict[int, CandidateParameterSource]:
    hardware = plan_input.hardware
    core_basis = hardware.physical_cores or hardware.logical_cores or 1
    logical_limit = hardware.logical_cores or core_basis
    source_kind = (
        ParameterSourceKind.FRACTION_PHYSICAL
        if hardware.physical_cores is not None
        else ParameterSourceKind.FRACTION_LOGICAL
    )
    label = "physical" if hardware.physical_cores is not None else "logical"
    values: dict[int, CandidateParameterSource] = {}
    for fraction in search.threads_batch.fractions_of_physical_cores:
        value = max(1, min(logical_limit, int(core_basis * fraction + 0.5)))
        values[value] = _source(
            source_kind,
            f"{fraction:.2f} of {core_basis} {label} cores for batch processing",
        )
    baseline = plan_input.baseline_runtime.threads_batch
    if search.threads_batch.include_baseline and baseline is not None and baseline <= logical_limit:
        values[baseline] = _source(
            ParameterSourceKind.BASELINE, "Exact baseline batch-processing thread count"
        )
    return dict(sorted(values.items()))


def _batch_pairs(search: SearchSpaceConfig) -> list[tuple[int, int, str]]:
    pairs: list[tuple[int, int, str]] = []
    for batch in sorted(search.batch_sizes):
        allowed = sorted(value for value in search.ubatch_sizes if value <= batch)
        if not allowed:
            continue
        targets = (batch, max(1, batch // 2), max(1, batch // 4))
        chosen = min(
            allowed, key=lambda value: (min(abs(value - target) for target in targets), value)
        )
        ratio = batch // chosen if batch % chosen == 0 else round(batch / chosen, 2)
        pairs.append((batch, chosen, f"Representative configured micro-batch ratio near 1/{ratio}"))
    return pairs


def _with(
    draft: Draft,
    *,
    label: str,
    stage: str,
    updates: dict[str, Any],
    sources: dict[str, CandidateParameterSource],
    rationale: tuple[str, ...],
    goal_tags: tuple[OptimizationGoal, ...] | None = None,
) -> Draft:
    updated_sources = draft.sources.copy()
    updated_sources.update(sources)
    return Draft(
        label=label,
        stage=stage,
        runtime=draft.runtime.model_copy(update=updates),
        goal_tags=goal_tags or draft.goal_tags,
        sources=updated_sources,
        rationale=rationale,
    )


def _goal_thread_subset(goal: OptimizationGoal, values: list[int]) -> list[int]:
    if len(values) <= 2:
        return values
    if goal is OptimizationGoal.LATENCY:
        return values[len(values) // 2 :]
    if goal is OptimizationGoal.THROUGHPUT:
        return values[-2:]
    if goal is OptimizationGoal.MEMORY:
        return values[:2]
    return values


def _goal_batch_subset(
    goal: OptimizationGoal, pairs: list[tuple[int, int, str]]
) -> list[tuple[int, int, str]]:
    if goal is OptimizationGoal.LATENCY:
        return pairs[1:3] or pairs
    if goal is OptimizationGoal.THROUGHPUT:
        return pairs[-2:]
    if goal is OptimizationGoal.MEMORY:
        return pairs[:2]
    return pairs


def _drafts(
    plan_input: SearchPlanInput,
    search: SearchSpaceConfig,
    goal: OptimizationGoal,
) -> tuple[list[Draft], list[PlanningExclusion]]:
    baseline_runtime = plan_input.baseline_runtime
    baseline = Draft(
        label="baseline",
        stage="baseline",
        runtime=baseline_runtime,
        goal_tags=(goal,),
        sources=_base_sources(baseline_runtime),
        rationale=("Preserves the exact baseline configuration or explicit runtime defaults",),
        baseline=True,
    )
    drafts = [baseline]
    exclusions = [
        PlanningExclusion(
            stage="bounded_generation",
            reason_code="cartesian_product_not_generated",
            reason=(
                "The planner intentionally uses representative staged changes instead of "
                "a full Cartesian product"
            ),
        ),
        PlanningExclusion(
            stage="cpu_affinity",
            reason_code="affinity_mapping_unavailable",
            reason=(
                "compact and spread affinity were omitted because v1 has no safe runtime "
                "mapping and does not invoke taskset"
            ),
        ),
        PlanningExclusion(
            stage="backend",
            reason_code="no_alternate_runtime_binary",
            reason=(
                "Backend/build comparison was omitted because planning received one runtime "
                "binary and does not invent alternate builds"
            ),
        ),
    ]
    thread_sources = generate_thread_values(plan_input, search)
    for threads in _goal_thread_subset(goal, list(thread_sources)):
        drafts.append(
            _with(
                baseline,
                label=goal.value,
                stage="thread_scaling",
                updates={"threads": threads},
                sources={"threads": thread_sources[threads]},
                rationale=(
                    f"Tests {threads} generation threads without assuming full-core "
                    "execution is fastest",
                    "Changes one primary dimension relative to the baseline",
                ),
            )
        )

    thread_batch_sources = _thread_batch_values(plan_input, search)
    batch_thread_values = list(thread_batch_sources)
    if goal in {OptimizationGoal.THROUGHPUT, OptimizationGoal.LATENCY}:
        batch_thread_values = batch_thread_values[-2:]
    elif goal is OptimizationGoal.MEMORY:
        batch_thread_values = batch_thread_values[:1]
    for threads_batch in batch_thread_values:
        if (
            not search.threads_batch.allow_greater_than_generation_threads
            and baseline_runtime.threads is not None
            and threads_batch > baseline_runtime.threads
        ):
            exclusions.append(
                PlanningExclusion(
                    stage="batch_thread_scaling",
                    reason_code="batch_threads_above_generation_disallowed",
                    reason="Configured policy disallows batch threads above generation threads",
                    proposed_runtime=baseline_runtime.model_copy(
                        update={"threads_batch": threads_batch}
                    ),
                )
            )
            continue
        drafts.append(
            _with(
                baseline,
                label=goal.value,
                stage="batch_thread_scaling",
                updates={"threads_batch": threads_batch},
                sources={"threads_batch": thread_batch_sources[threads_batch]},
                rationale=(
                    f"Tests {threads_batch} batch-processing threads independently from "
                    "generation threads",
                ),
            )
        )

    pairs = _goal_batch_subset(goal, _batch_pairs(search))
    for batch, ubatch, reason in pairs:
        drafts.append(
            _with(
                baseline,
                label=goal.value,
                stage="batch_microbatch",
                updates={"batch_size": batch, "ubatch_size": ubatch},
                sources={
                    "batch_size": _source(
                        ParameterSourceKind.SEARCH_SPACE, "Configured representative batch size"
                    ),
                    "ubatch_size": _source(ParameterSourceKind.SEARCH_SPACE, reason),
                },
                rationale=(
                    f"Tests batch {batch} with bounded micro-batch {ubatch}",
                    "Preserves ubatch_size <= batch_size",
                ),
            )
        )

    core_limit = plan_input.hardware.physical_cores or plan_input.hardware.logical_cores or 1
    for slots in GOAL_PREFERENCES[goal].parallel_order:
        if slots not in search.parallel_slots:
            continue
        if slots > 1 and core_limit < slots * 2:
            exclusions.append(
                PlanningExclusion(
                    stage="parallelism",
                    reason_code="insufficient_cores_for_parallelism",
                    reason=(
                        f"parallel_slots={slots} omitted because {core_limit} core-basis "
                        "cores provide limited headroom"
                    ),
                    proposed_runtime=baseline_runtime.model_copy(update={"parallel_slots": slots}),
                )
            )
            continue
        drafts.append(
            _with(
                baseline,
                label=goal.value,
                stage="goal_parallelism",
                updates={"parallel_slots": slots},
                sources={
                    "parallel_slots": _source(
                        ParameterSourceKind.GOAL_SPECIFIC,
                        f"{goal.value} goal parallelism preference",
                    )
                },
                rationale=(
                    (
                        "Keeps one slot to isolate sequential request behavior"
                        if slots == 1
                        else f"Tests {slots} slots for aggregate concurrency without "
                        "claiming a throughput gain"
                    ),
                ),
            )
        )

    for cache in search.prompt_cache:
        if cache == baseline_runtime.prompt_cache:
            continue
        drafts.append(
            _with(
                baseline,
                label=goal.value,
                stage="prompt_cache",
                updates={"prompt_cache": cache},
                sources={
                    "prompt_cache": _source(
                        ParameterSourceKind.SEARCH_SPACE,
                        "Explicit configured prompt-cache comparison",
                    )
                },
                rationale=(
                    "Candidate tests whether repeated prompt prefixes benefit from prompt caching",
                ),
            )
        )
    for mmap in search.mmap:
        if mmap == baseline_runtime.mmap:
            continue
        drafts.append(
            _with(
                baseline,
                label=goal.value,
                stage="memory_mapping",
                updates={"mmap": mmap},
                sources={
                    "mmap": _source(
                        ParameterSourceKind.SEARCH_SPACE,
                        "Explicit configured mmap comparison",
                    )
                },
                rationale=(
                    "Tests a memory-mapping alternative without assuming its memory or "
                    "latency effect",
                ),
            )
        )

    if search.enable_numa_experiments and (plan_input.hardware.numa_nodes or 0) > 1:
        for mode in search.numa_modes:
            if mode == baseline_runtime.numa_mode:
                continue
            drafts.append(
                _with(
                    baseline,
                    label=goal.value,
                    stage="numa",
                    updates={"numa_mode": mode},
                    sources={
                        "numa_mode": _source(
                            ParameterSourceKind.SEARCH_SPACE,
                            "Explicit multi-NUMA-node experiment",
                        )
                    },
                    rationale=(f"Tests NUMA mode {mode} on a machine reporting multiple nodes",),
                )
            )
    else:
        exclusions.append(
            PlanningExclusion(
                stage="numa",
                reason_code="numa_experiments_not_applicable",
                reason=(
                    "NUMA alternatives were not generated because experiments are disabled "
                    "or fewer than two nodes were detected"
                ),
            )
        )

    if search.context.policy == "explicit":
        for context in search.context.explicit_sizes:
            if (
                baseline_runtime.context_size is not None
                and context < baseline_runtime.context_size
            ):
                exclusions.append(
                    PlanningExclusion(
                        stage="context",
                        reason_code="context_below_baseline",
                        reason=(
                            "Context reduction was omitted because workload capacity cannot "
                            "be proven safe"
                        ),
                        proposed_runtime=baseline_runtime.model_copy(
                            update={"context_size": context}
                        ),
                    )
                )
                continue
            drafts.append(
                _with(
                    baseline,
                    label=goal.value,
                    stage="context",
                    updates={"context_size": context},
                    sources={
                        "context_size": _source(
                            ParameterSourceKind.SEARCH_SPACE,
                            "Explicit context experiment; workload token capacity remains "
                            "uncertain",
                        )
                    },
                    rationale=(
                        "Tests an explicitly configured context size and treats it as a "
                        "memory-sensitive change",
                    ),
                )
            )

    smallest_batch = min(search.batch_sizes)
    smallest_ubatch = min(value for value in search.ubatch_sizes if value <= smallest_batch)
    drafts.append(
        _with(
            baseline,
            label="memory",
            stage="memory_conscious",
            updates={
                "threads": min(thread_sources),
                "batch_size": smallest_batch,
                "ubatch_size": smallest_ubatch,
                "parallel_slots": 1,
            },
            sources={
                "threads": thread_sources[min(thread_sources)],
                "batch_size": _source(ParameterSourceKind.GOAL_SPECIFIC, "Small configured batch"),
                "ubatch_size": _source(
                    ParameterSourceKind.GOAL_SPECIFIC, "Small valid configured micro-batch"
                ),
                "parallel_slots": _source(
                    ParameterSourceKind.GOAL_SPECIFIC, "Single-slot memory-conscious experiment"
                ),
            },
            rationale=(
                "Combines conservative bounded settings as a memory-conscious experiment",
                "Does not claim these settings minimize resident memory before measurement",
            ),
            goal_tags=(goal, OptimizationGoal.MEMORY),
        )
    )
    return drafts, exclusions


def _diverse_select(
    candidates: list[CandidateProfile], maximum: int
) -> tuple[list[CandidateProfile], list[CandidateProfile]]:
    if len(candidates) <= maximum:
        return candidates, []
    baseline = next(candidate for candidate in candidates if candidate.baseline)
    selected = [baseline]
    remaining = [candidate for candidate in candidates if not candidate.baseline]
    covered: dict[str, set[object]] = {
        "stage": {baseline.stage},
        "threads": {baseline.runtime.threads},
        "batch": {baseline.runtime.batch_size},
        "ubatch": {baseline.runtime.ubatch_size},
        "parallel": {baseline.runtime.parallel_slots},
        "cache": {baseline.runtime.prompt_cache},
        "mmap": {baseline.runtime.mmap},
    }
    while remaining and len(selected) < maximum:

        def diversity_key(candidate: CandidateProfile) -> tuple[int, str]:
            values = {
                "stage": candidate.stage,
                "threads": candidate.runtime.threads,
                "batch": candidate.runtime.batch_size,
                "ubatch": candidate.runtime.ubatch_size,
                "parallel": candidate.runtime.parallel_slots,
                "cache": candidate.runtime.prompt_cache,
                "mmap": candidate.runtime.mmap,
            }
            gain = sum(value not in covered[name] for name, value in values.items())
            return (-gain, candidate.id)

        chosen = min(remaining, key=diversity_key)
        selected.append(chosen)
        remaining.remove(chosen)
        covered["stage"].add(chosen.stage)
        covered["threads"].add(chosen.runtime.threads)
        covered["batch"].add(chosen.runtime.batch_size)
        covered["ubatch"].add(chosen.runtime.ubatch_size)
        covered["parallel"].add(chosen.runtime.parallel_slots)
        covered["cache"].add(chosen.runtime.prompt_cache)
        covered["mmap"].add(chosen.runtime.mmap)
    return selected, remaining


def generate_candidates(
    plan_input: SearchPlanInput,
    capabilities: ServerCapabilities,
    search: SearchSpaceConfig,
    goal: OptimizationGoal,
    maximum_profiles: int,
) -> GenerationResult:
    drafts, exclusions = _drafts(plan_input, search, goal)
    unique: dict[str, Draft] = {}
    for draft in drafts:
        digest = profile_hash(draft.runtime)
        if digest in unique:
            exclusions.append(
                PlanningExclusion(
                    stage=draft.stage,
                    candidate_id=profile_id(draft.label, draft.runtime),
                    profile_hash=digest,
                    reason_code="duplicate_effective_configuration",
                    reason="Equivalent effective runtime configuration was already represented",
                    proposed_runtime=draft.runtime,
                )
            )
        else:
            unique[digest] = draft

    profiles: list[CandidateProfile] = []
    used_ids: set[str] = set()
    for digest, draft in unique.items():
        candidate_id = profile_id(draft.label, draft.runtime, baseline=draft.baseline)
        if candidate_id in used_ids:
            candidate_id = f"{candidate_id}-{digest[:6]}"
        used_ids.add(candidate_id)
        compatibility = check_candidate_compatibility(draft.runtime, capabilities)
        resource = assess_memory_risk(draft.runtime, plan_input)
        profile = CandidateProfile(
            id=candidate_id,
            profile_hash=digest,
            stage=draft.stage,
            baseline=draft.baseline,
            executable=compatibility.compatible
            and resource.classification is not MemoryRiskClass.HIGH_RISK,
            goal_tags=list(dict.fromkeys(draft.goal_tags)),
            runtime=draft.runtime,
            parameter_sources=draft.sources,
            rationale=list(draft.rationale),
            compatibility=compatibility,
            resource_estimate=resource,
        )
        if not compatibility.compatible or resource.classification is MemoryRiskClass.HIGH_RISK:
            reason_code = (
                "unsupported_configuration"
                if not compatibility.compatible
                else "memory_guardrail_high_risk"
            )
            exclusions.append(
                PlanningExclusion(
                    stage=draft.stage,
                    candidate_id=candidate_id,
                    profile_hash=digest,
                    reason_code=reason_code,
                    reason=(
                        "Candidate requests settings unsupported by the inspected runtime"
                        if not compatibility.compatible
                        else resource.reason
                    ),
                    proposed_runtime=draft.runtime,
                    compatibility_details=compatibility.details,
                )
            )
        else:
            profiles.append(profile)

    profiles.sort(key=lambda candidate: (not candidate.baseline, candidate.stage, candidate.id))
    if not any(candidate.baseline for candidate in profiles):
        raise PlanningError(
            "The baseline configuration cannot be represented by the inspected runtime; "
            "no executable search plan can preserve the required baseline"
        )
    selected, truncated = _diverse_select(profiles, maximum_profiles)
    for profile in truncated:
        exclusions.append(
            PlanningExclusion(
                stage=profile.stage,
                candidate_id=profile.id,
                profile_hash=profile.profile_hash,
                reason_code="profile_limit_diversity_selection",
                reason=(
                    "Compatible candidate omitted by deterministic diversity selection "
                    "under the profile limit"
                ),
                proposed_runtime=profile.runtime,
            )
        )
    warnings: list[PlanningWarning] = []
    if len(selected) < search.limits.minimum_profiles:
        warnings.append(
            PlanningWarning(
                code="minimum_profile_count_unmet",
                message=(
                    f"Only {len(selected)} compatible unique profiles could be planned; "
                    f"configured minimum is {search.limits.minimum_profiles}"
                ),
            )
        )
    if not plan_input.hardware.is_arm64:
        warnings.append(
            PlanningWarning(
                code="non_arm_development_plan",
                message="Current platform is not AArch64; this plan is development evidence only",
            )
        )
    if plan_input.model.synthetic_fixture:
        warnings.append(
            PlanningWarning(
                code="synthetic_planning_fixture",
                message="Synthetic planning fixture — not Arm performance evidence",
            )
        )
    warnings.extend(
        PlanningWarning(code="provenance_difference", message=item.reason)
        for item in (
            plan_input.baseline.compatibility.differences if plan_input.baseline is not None else []
        )
    )
    return GenerationResult(
        candidates=selected,
        exclusions=sorted(
            exclusions,
            key=lambda item: (item.stage, item.reason_code, item.candidate_id or ""),
        ),
        warnings=sorted(warnings, key=lambda item: (item.code, item.message)),
        unique_generated=len(unique),
    )
