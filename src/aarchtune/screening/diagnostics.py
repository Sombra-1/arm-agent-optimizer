"""Offline explanation of persisted screening decisions; never runs inference."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import TypeVar, cast

from pydantic import BaseModel, JsonValue

from aarchtune.evaluation.models import CandidateExecutionResult, CandidateExecutionStatus
from aarchtune.optimization.models import SearchPlan
from aarchtune.screening.models import (
    BenchExecutionResult,
    BenchSignature,
    CandidateAdvancementDecision,
    DecisionStatus,
    MatrixEntry,
    NormalizedBenchMeasurement,
    ScreeningManifest,
    ScreeningModel,
    SignatureMembership,
    SignatureScreeningResult,
)

FAILURE_ACTIONS = {
    "total_timeout": "Reduce the planned search or increase its time budget before rerunning.",
    "process_start_failure": (
        "Check the benchmark executable, permissions, and local failure metadata."
    ),
    "timeout": "Inspect resource pressure and scenario cost before increasing invocation timeouts.",
    "nonzero_exit": "Inspect local benchmark stderr, supported flags, and available memory.",
    "parser_failure": (
        "Check the pinned benchmark's machine-readable output format and parser support."
    ),
    "settings_mismatch": (
        "Check benchmark flag mappings and observed settings before trusting results."
    ),
    "target_row_missing": (
        "Check scenario flags and the benchmark's reported prompt/generation counts."
    ),
    "target_row_ambiguous": (
        "Check why the benchmark returned multiple rows for the requested scenario."
    ),
    "invalid_target_measurement": "Check missing or invalid throughput and measurement provenance.",
    "unstable_measurements": "Reduce competing load and repeat measurements on a controlled host.",
    "unsupported_bench_mapping": "Use supported benchmark settings or a compatible pinned runtime.",
    "low_screening_score": (
        "Consider a larger advancement limit if broader workload coverage is needed."
    ),
    "duplicate_server_profile": (
        "Consider a larger advancement limit to evaluate more server-only variants."
    ),
    "memory_guardrail_high_risk": (
        "Reduce context/slots, use a smaller model, or add memory before regenerating the plan."
    ),
}


class ScreeningDiagnosis(ScreeningModel):
    screening_id: str | None
    status: str | None
    synthetic: bool | None
    evidence_errors: list[str]
    source_sha256: dict[str, str]
    coverage: dict[str, int | float | None]
    candidates: list[dict[str, JsonValue]]
    planning_exclusions: list[dict[str, JsonValue]]
    invocations: list[dict[str, JsonValue]]
    next_steps: list[str]
    limitations: list[str]


class _Failure(ScreeningModel):
    invocation_id: str
    code: str
    reason: str = ""


ModelT = TypeVar("ModelT", bound=BaseModel)


class _Evidence:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.hashes: dict[str, str] = {}

    def load(self, path: Path, model: type[ModelT], *, lines: bool = False) -> list[ModelT] | None:
        try:
            if path.is_symlink():
                raise ValueError("Symlinked metadata")
            content = path.read_bytes()
            self.hashes[path.name] = hashlib.sha256(content).hexdigest()
            records = content.splitlines() if lines else [content]
            return [model.model_validate_json(row) for row in records if row.strip()]
        except (OSError, ValueError) as exc:
            # Validation errors can quote input. Preserve only kind and filename.
            self.errors.append(f"Unavailable metadata: {path.name} ({type(exc).__name__})")
            return None

    def one(self, path: Path, model: type[ModelT]) -> ModelT | None:
        values = self.load(path, model)
        if values:
            return values[0]
        if values == []:
            self.errors.append(f"Empty metadata: {path.name}")
        return None


def diagnose_screening(
    screening_dir: Path,
    *,
    evaluated: list[CandidateExecutionResult] | None = None,
) -> ScreeningDiagnosis:
    """Explain recorded facts; unavailable evidence is never interpreted as zero."""

    root = screening_dir.expanduser().resolve()
    evidence = _Evidence()
    manifest = evidence.one(root / "screening-manifest.json", ScreeningManifest)
    plan = None
    if manifest and manifest.search_plan_reference:
        plan = evidence.one(manifest.search_plan_reference.path / "search-plan.json", SearchPlan)
        if plan and plan.plan_hash != manifest.search_plan_reference.plan_hash:
            evidence.errors.append("Search-plan hash differs from the screening reference")
            plan = None
    else:
        evidence.errors.append("Search-plan reference is unavailable")
    signatures = evidence.load(root / "bench-signatures.jsonl", BenchSignature, lines=True)
    memberships = evidence.load(
        root / "signature-membership.jsonl", SignatureMembership, lines=True
    )
    matrix = evidence.load(root / "benchmark-matrix.jsonl", MatrixEntry, lines=True)
    executions = evidence.load(root / "raw-executions.jsonl", BenchExecutionResult, lines=True)
    measurements = evidence.load(
        root / "normalized-measurements.jsonl", NormalizedBenchMeasurement, lines=True
    )
    failures = evidence.load(root / "failures.jsonl", _Failure, lines=True)
    results = evidence.load(root / "signature-results.jsonl", SignatureScreeningResult, lines=True)
    decisions = evidence.load(
        root / "advancement-decisions.jsonl", CandidateAdvancementDecision, lines=True
    )
    execution_by_id = {item.invocation_id: item for item in executions or []}
    failures_by_id: dict[str, set[str]] = defaultdict(set)
    for failure in failures or []:
        failures_by_id[failure.invocation_id].add(failure.code)
    measured_by_id: dict[str, list[NormalizedBenchMeasurement]] = defaultdict(list)
    for measurement in measurements or []:
        measured_by_id[measurement.invocation_id].append(measurement)
    attempted = set(execution_by_id) | {
        key for key, codes in failures_by_id.items() if codes - {"total_timeout"}
    }
    usable = {
        item.invocation_id
        for item in measurements or []
        if item.screening_usable
        and item.matches_requested_scenario
        and item.provenance_valid
        and item.invocation_id in execution_by_id
        and execution_by_id[item.invocation_id].exit_code == 0
        and not execution_by_id[item.invocation_id].timed_out
        and not execution_by_id[item.invocation_id].interrupted
        and not failures_by_id[item.invocation_id]
    }
    invocations: list[dict[str, JsonValue]] = []
    codes_seen: set[str] = set()
    if plan:
        codes_seen.update(item.reason_code for item in plan.excluded_possibilities)
    entries_by_signature: dict[str, list[MatrixEntry]] = defaultdict(list)
    for entry in matrix or []:
        entries_by_signature[entry.signature_id].append(entry)
        execution = execution_by_id.get(entry.invocation_id)
        codes = sorted(failures_by_id[entry.invocation_id])
        codes_seen.update(codes)
        status = (
            "timed_out"
            if execution and execution.timed_out
            else "interrupted"
            if execution and execution.interrupted
            else "execution_failed"
            if execution and execution.exit_code != 0
            else "measurement_failed"
            if execution and codes
            else "usable"
            if entry.invocation_id in usable
            else "not_attempted_budget"
            if "total_timeout" in codes and not execution
            else "process_start_failed"
            if "process_start_failure" in codes
            else "not_recorded"
        )
        invocations.append(
            cast(
                dict[str, JsonValue],
                {
                    "invocation_id": entry.invocation_id,
                    "signature_id": entry.signature_id,
                    "scenario_id": entry.scenario_id,
                    "repetition": entry.repetition,
                    "status": status,
                    "failure_codes": codes,
                    "exit_code": execution.exit_code if execution else None,
                    "measurement_checks": [
                        {
                            "target_row": item.matches_requested_scenario,
                            "usable": item.screening_usable,
                            "provenance_valid": item.provenance_valid,
                            "provenance_errors": list(item.provenance_errors),
                        }
                        for item in measured_by_id[entry.invocation_id]
                    ],
                },
            )
        )
    member_by_id = {item.candidate_id: item for item in memberships or []}
    result_by_id = {item.signature_id: item for item in results or []}
    decision_by_id = {item.candidate_id: item for item in decisions or []}
    profile_by_id = {item.id: item for item in plan.candidates} if plan else {}
    evaluated_by_id: dict[str, CandidateExecutionResult] = {}
    evaluation_consistent = True
    for item in evaluated or []:
        profile = profile_by_id.get(item.candidate_id)
        if profile and profile.profile_hash == item.candidate_hash:
            evaluated_by_id[item.candidate_id] = item
        else:
            evidence.errors.append("Workload evaluation profile differs from the screening plan")
            evaluation_consistent = False
    candidates: list[dict[str, JsonValue]] = []
    known_ids = set(profile_by_id) | set(member_by_id) | set(decision_by_id)
    for candidate_id in sorted(known_ids):
        member = member_by_id.get(candidate_id)
        profile = profile_by_id.get(candidate_id)
        decision = decision_by_id.get(candidate_id)
        result = result_by_id.get(member.bench_signature_id) if member else None
        candidate_invocations = entries_by_signature[member.bench_signature_id] if member else []
        failure_codes = sorted(
            {code for item in candidate_invocations for code in failures_by_id[item.invocation_id]}
        )
        reasons = list(result.reasons) if result else []
        if decision:
            codes_seen.add(decision.reason_code)
        codes_seen.update(reasons)
        workload_result = evaluated_by_id.get(candidate_id)
        candidate_codes = set(failure_codes) | set(reasons)
        if decision:
            candidate_codes.add(decision.reason_code)
        if result and result.status.value == "unsupported":
            candidate_codes.add("unsupported_bench_mapping")
            codes_seen.add("unsupported_bench_mapping")
        candidates.append(
            cast(
                dict[str, JsonValue],
                {
                    "id": candidate_id,
                    "profile_hash": profile.profile_hash if profile else None,
                    "baseline": profile.baseline if profile else None,
                    "settings": profile.runtime.model_dump(mode="json") if profile else None,
                    "signature_id": member.bench_signature_id if member else None,
                    "shares_signature_with": sorted(
                        item.candidate_id
                        for item in memberships or []
                        if member
                        and item.bench_signature_id == member.bench_signature_id
                        and item.candidate_id != candidate_id
                    ),
                    "server_only_fields": [item.field for item in member.unscreenable_fields]
                    if member
                    else [],
                    "attempted_signature_invocations": sum(
                        item.invocation_id in attempted for item in candidate_invocations
                    )
                    if member is not None
                    and matrix is not None
                    and executions is not None
                    and failures is not None
                    else None,
                    "decision": decision.decision.value if decision else "not_recorded",
                    "reason_code": decision.reason_code if decision else None,
                    "reason": decision.reason if decision else None,
                    "signature_status": result.status.value if result else None,
                    "screening_eligible": result.screening_eligible if result else None,
                    "screening_reasons": reasons,
                    "failure_codes": failure_codes,
                    "next_steps": sorted(
                        {
                            FAILURE_ACTIONS[code]
                            for code in candidate_codes
                            if code in FAILURE_ACTIONS
                        }
                    ),
                    "scenario_results": [
                        item.model_dump(mode="json") for item in result.scenario_aggregates
                    ]
                    if result
                    else [],
                    "workload_execution_status": (
                        workload_result.status.value if workload_result else None
                    ),
                },
            )
        )
    planned = len(plan.candidates) if plan else None
    expected = len(matrix) if matrix is not None else None
    completed = (
        sum(item.status is CandidateExecutionStatus.COMPLETED for item in evaluated_by_id.values())
        if evaluated is not None and evaluation_consistent
        else None
    )
    coverage = {
        "planned_profiles": planned,
        "planning_exclusions": len(plan.excluded_possibilities) if plan else None,
        "low_level_signatures": len(signatures) if signatures is not None else None,
        "planned_invocations": expected,
        "attempted_invocations": len(attempted)
        if executions is not None and failures is not None
        else None,
        "usable_invocations": len(usable)
        if measurements is not None and executions is not None and failures is not None
        else None,
        "usable_invocation_fraction": len(usable) / expected
        if expected and measurements is not None and executions is not None and failures is not None
        else None,
        "advanced_profiles": sum(item.decision is DecisionStatus.ADVANCED for item in decisions)
        if decisions is not None
        else None,
        "workload_completed_profiles": completed,
        "workload_completed_fraction": completed / planned
        if completed is not None and planned
        else None,
    }
    next_steps = sorted({FAILURE_ACTIONS[code] for code in codes_seen if code in FAILURE_ACTIONS})
    if evidence.errors:
        next_steps.insert(
            0, "Restore missing or invalid metadata before drawing coverage conclusions."
        )
    if any(item["server_only_fields"] for item in candidates):
        next_steps.append(
            "Use real-workload evaluation to validate server-only settings; shared benchmark "
            "measurements do not test those settings."
        )
    return ScreeningDiagnosis(
        screening_id=manifest.screening_id if manifest else None,
        status=manifest.status.value if manifest else None,
        synthetic=plan.summary.synthetic_fixture if plan else None,
        evidence_errors=evidence.errors,
        source_sha256=evidence.hashes,
        coverage=coverage,
        candidates=candidates,
        planning_exclusions=[item.model_dump(mode="json") for item in plan.excluded_possibilities]
        if plan
        else [],
        invocations=invocations,
        next_steps=next_steps,
        limitations=[
            "This diagnoses recorded metadata; it is not integrity validation or a new benchmark.",
            "Shared low-level signatures do not establish server-only behavior or quality.",
            "Workload coverage is unavailable until validated evaluation results are supplied.",
            "Raw benchmark output, logs, and generated model responses are not read or included.",
        ],
    )
