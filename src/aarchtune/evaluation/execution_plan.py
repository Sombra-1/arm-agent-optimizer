"""Deterministic immutable evaluation plan construction."""

from __future__ import annotations

from aarchtune.evaluation.models import EvaluationConfig, EvaluationPlan, QualityPolicySource
from aarchtune.evaluation.provenance import LoadedEvaluationInput
from aarchtune.optimization.identity import stable_hash
from aarchtune.workload.loader import load_workload


def build_evaluation_plan(
    evaluation_id: str,
    config: EvaluationConfig,
    source: LoadedEvaluationInput,
    policy: QualityPolicySource,
) -> EvaluationPlan:
    workload = load_workload(source.current_input.workload.path)
    order = ["baseline-start", *[item.profile.id for item in source.candidates], "baseline-end"]
    semantic = {
        "evaluation_id": evaluation_id,
        "goal": source.search_plan.goal.value,
        "candidate_hashes": [item.profile.profile_hash for item in source.candidates],
        "task_order": [task.id for task in workload.tasks],
        "repetitions": config.repetitions,
        "warmup": config.warmup_requests,
        "request_timeout": config.request_timeout_seconds,
        "startup_timeout": config.startup_timeout_seconds,
        "sample_interval": config.sample_interval_seconds,
        "settling_delay": config.settling_delay_seconds,
        "quality_policy": policy.sha256,
    }
    return EvaluationPlan(
        evaluation_id=evaluation_id,
        plan_hash=stable_hash(semantic),
        goal=source.search_plan.goal,
        baseline_start_profile=source.baseline_profile,
        candidates=source.candidates,
        baseline_end_profile=source.baseline_profile,
        execution_order=order,
        task_order=[task.id for task in workload.tasks],
        warmup_requests=config.warmup_requests,
        repetitions=config.repetitions,
        request_timeout_seconds=config.request_timeout_seconds,
        startup_timeout_seconds=config.startup_timeout_seconds,
        sample_interval_seconds=config.sample_interval_seconds,
        settling_delay_seconds=config.settling_delay_seconds,
        quality_policy_sha256=policy.sha256,
        expected_attempt_count=len(workload.tasks)
        * config.repetitions
        * (len(source.candidates) + 2),
        maximum_candidate_failures=config.maximum_candidate_failures,
        maximum_total_duration_seconds=config.maximum_total_duration_seconds,
        deterministic_ordering_bias=(
            "Stable execution order can introduce thermal and temporal bias; baseline-end is "
            "a drift sentinel, not complete bias elimination."
        ),
    )
