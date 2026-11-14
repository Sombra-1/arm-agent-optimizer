#!/usr/bin/env python3
"""Controlled native 0.2 experiments; public projections never contain model text."""

from __future__ import annotations

import argparse
import json
import platform
import signal
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx
from collect_screening_diagnostics import Redactor, collect_diagnostics

from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.benchmark.models import RawAttemptRecord, RequestMeasurement
from aarchtune.evaluation.quality_policy import load_quality_policy
from aarchtune.finalization.models import ReportData
from aarchtune.finalization.passport import verify_passport
from aarchtune.finalization.report import render_report
from aarchtune.finalization.validation import validate_bundle
from aarchtune.optimization.models import OptimizationGoal
from aarchtune.orchestration.models import OptimizeConfig
from aarchtune.orchestration.runner import run_optimization
from aarchtune.orchestration.validation import validate_optimization
from aarchtune.preflight.models import PreflightReport
from aarchtune.preflight.report import write_report
from aarchtune.runtime.capabilities import inspect_llama_server_capabilities
from aarchtune.runtime.config import LlamaServerConfig, ResponseFormatMode
from aarchtune.runtime.llama_server import LlamaServerProcess
from aarchtune.workload.evaluation import evaluate_workload
from aarchtune.workload.loader import load_workload

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return value


def audit_requests(root: Path, workload_path: Path, mode: ResponseFormatMode) -> dict[str, Any]:
    """Prove the actual measured requests retain the workload's generation contract."""

    tasks = {task.id: task for task in load_workload(workload_path).tasks}
    files = sorted(root.rglob("request-metrics.jsonl"))
    errors: list[str] = []
    counts: dict[str, int] = {}
    for path in files:
        count = 0
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            measurement = RequestMeasurement.model_validate_json(line)
            task = tasks[measurement.task_id]
            request = measurement.request
            correct = (
                request.response_format_mode is mode
                and request.response_format_type == mode.response_type
                and request.response_format_applied == (mode is not ResponseFormatMode.NONE)
                and request.temperature == task.generation.temperature
                and request.max_tokens == task.generation.max_tokens
                and request.seed == task.generation.seed
                and request.stream is False
                and request.response_format_schema
                == (task.output_schema if mode is ResponseFormatMode.JSON_SCHEMA else None)
            )
            if not correct:
                errors.append(f"Generation contract changed in {path.relative_to(root)}")
            count += 1
        counts[str(path.relative_to(root))] = count
    if not counts or not all(counts.values()):
        errors.append("Measured request evidence is absent or empty")
    return {"passed": not errors, "errors": errors, "request_counts": counts}


def probe_output_modes(binary: Path, model: Path, workload_path: Path) -> dict[str, Any]:
    """Single-task protocol checks, kept separate from full workload quality evidence."""

    workload = load_workload(workload_path)
    task = workload.tasks[0]
    one_task = workload.model_copy(update={"tasks": [task]})
    modes: dict[str, Any] = {}
    config = LlamaServerConfig(
        binary_path=binary,
        model_path=model,
        startup_timeout_seconds=120.0,
        request_timeout_seconds=90.0,
        context_size=2048,
        parallel_slots=1,
    )
    capabilities = inspect_llama_server_capabilities(binary)
    with LlamaServerProcess(config, capabilities) as server:
        server.wait_until_ready()
        for mode in ResponseFormatMode:
            server.client.response_format_mode = mode
            response = server.client.chat_completion(task)
            quality = evaluate_workload(one_task, [response])
            modes[mode.value] = {
                "request_succeeded": response.request_succeeded,
                "json_validity_rate": quality.json_validity_rate,
                "task_success_rate": quality.task_success_rate,
                "scope": "one-task protocol probe; not a quality-policy result",
            }
        with httpx.Client(trust_env=False) as client:
            response = client.post(
                f"{server.client.base_url}/v1/chat/completions",
                json={
                    "messages": [message.model_dump(mode="json") for message in task.messages],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                    "response_format": {"type": "aarchtune_unsupported_format"},
                },
                timeout=90.0,
            )
            modes["unsupported"] = {
                "status_code": response.status_code,
                "rejected": response.status_code in {400, 422},
            }
    return modes


def summarize_run(root: Path, redactor: Redactor, public: Path) -> dict[str, Any]:
    """Project known response-free fields; validate original artifacts before publishing."""

    validation = validate_optimization(root)
    manifest = read_json(root / "optimize-manifest.json")
    summary = read_json(root / "baseline/baseline-summary.json")
    public.mkdir(parents=True, exist_ok=True)
    report = PreflightReport.model_validate_json((root / "preflight/preflight.json").read_bytes())
    sanitized = PreflightReport.model_validate_json(
        json.dumps(redactor.value(report.model_dump(mode="json")))
    )
    write_report(sanitized, public / "preflight")
    result: dict[str, Any] = {
        "validation": validation.model_dump(mode="json"),
        "stages": [stage["stage"] for stage in manifest["stages"]],
        "cleanup_confirmed": manifest["owned_processes_stopped"] is True
        and manifest["samplers_stopped"] is True,
        "native": summary["is_arm64"] and not summary["synthetic_fixture"],
        "baseline_quality": summary["quality"],
        "preflight_outcome": report.outcome,
        "runtime_version": summary["runtime_version"],
        "source_artifact_sha256": {
            name: hash_file_streaming(root / name)
            for name in (
                "optimize-manifest.json",
                "baseline/baseline-summary.json",
                "baseline/raw-attempts.jsonl",
                "preflight/preflight.json",
            )
        },
    }
    locations: dict[str, Counter[str]] = defaultdict(Counter)
    for line in (root / "baseline/raw-attempts.jsonl").read_text().splitlines():
        record = RawAttemptRecord.model_validate_json(line)
        for validator in record.validation.validator_results:
            if not validator.passed:
                locations[record.task_id][
                    f"{validator.validator.value}:{validator.path or '$'}"
                ] += 1
    result["baseline_failed_validator_locations"] = {
        task: dict(counts) for task, counts in locations.items()
    }
    if (root / "screening").is_dir():
        collect_diagnostics(optimize_dir=root, evidence_dir=public, redactor=redactor)
        result["screening_diagnostics"] = {
            "included": True,
            "path": "screening-diagnostics/screening-diagnostics.json",
        }
    if (root / "final/bundle-manifest.json").is_file():
        final = validate_bundle(root / "final")
        passport = verify_passport(root / "final/optimization-passport.json")
        result["final_validation"] = final.model_dump(mode="json")
        result["passport_validation"] = passport.model_dump(mode="json")
        result["selection"] = read_json(root / "evaluation/selection.json")
        result["drift"] = read_json(root / "evaluation/drift-assessment.json")
        data = read_json(root / "final/report-data.json")
        result["candidates"] = data["candidates"]
        result["improvements"] = data["hero"]["improvements"]
        result["source_artifact_sha256"]["final/optimization-passport.json"] = hash_file_streaming(
            root / "final/optimization-passport.json"
        )
        clean_data = redactor.value(data)
        write_json(public / "report-data.json", clean_data)
        (public / "report.html").write_text(
            render_report(ReportData.model_validate_json(json.dumps(clean_data)))
        )
    return redactor.value(result)  # type: ignore[no-any-return]


def assess_experiment(experiment: str, result: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    if not result["validation"]["valid"] or not result["native"] or not result["cleanup_confirmed"]:
        errors.append("Native evidence validation or process cleanup failed")
    if not result["request_audit"]["passed"]:
        errors.append("Generation settings changed between baseline and candidates")
    if experiment == "rejection":
        if result["preflight_outcome"] != "quality_failed":
            errors.append("Historical poor-quality rejection was not reproduced")
        if result["stages"] != ["doctor", "baseline"] or any(
            (root / stage).exists() for stage in ("plan", "screening", "evaluation", "final")
        ):
            errors.append("Costly search stages ran after a failing baseline")
    else:
        if result["stages"] != [
            "doctor",
            "baseline",
            "planning",
            "screening",
            "evaluation",
            "finalization",
        ]:
            errors.append("Full native pipeline did not complete")
        if not result.get("final_validation", {}).get("valid") or not result.get(
            "passport_validation", {}
        ).get("valid"):
            errors.append("Final bundle or optimization passport failed verification")
        if result.get("selection", {}).get("outcome") not in {
            "candidate_selected",
            "baseline_retained",
        }:
            errors.append("No quality-passing, drift-valid deployment result was established")
    return errors


def compare_repeats(runs: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = [
        {candidate["hash"]: candidate["quality_status"] for candidate in run["candidates"]}
        for run in runs
    ]
    shared = set.intersection(*(set(run) for run in decisions))
    baselines = [
        next(candidate for candidate in run["candidates"] if candidate["baseline"]) for run in runs
    ]
    p95_values = [baseline["p95_latency_seconds"] for baseline in baselines]
    return {
        "candidate_sets_identical": all(set(run) == set(decisions[0]) for run in decisions),
        "shared_candidates": len(shared),
        "quality_decisions_consistent_for_shared_candidates": bool(shared)
        and all(len({run[candidate] for run in decisions}) == 1 for candidate in shared),
        "baseline_p95_latency_seconds": p95_values,
        "baseline_p95_relative_range": (
            (max(p95_values) - min(p95_values)) / min(p95_values)
            if all(value is not None and value > 0 for value in p95_values)
            else None
        ),
        "scope": "Descriptive same-runner repetitions, "
        "not statistical confidence or independent hosts.",
    }


def capture_failure(
    root: Path, public: Path, redactor: Redactor, error: BaseException
) -> dict[str, Any]:
    """Retain stage state and typed failure locations without exception/body text."""

    public.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {"error_chain": [], "stage_state": {}}
    current: BaseException | None = error
    while current is not None:
        state["error_chain"].append(
            {
                "type": type(current).__name__,
                "frames": [
                    {
                        "file": redactor.text(frame.filename),
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in traceback.extract_tb(current.__traceback__)
                ],
            }
        )
        if type(current).__name__ in {
            "BaselineReferenceError",
            "PlanningError",
            "ProvenanceMismatchError",
            "PlanArtifactError",
            "ScenarioError",
            "BenchCommandError",
            "EvaluationInputError",
        }:
            # These errors describe planning/benchmark metadata, never completions.
            state["error_chain"][-1]["metadata_error"] = redactor.text(str(current))
        current = current.__cause__
    for relative in (
        "optimize-manifest.json",
        "baseline/manifest.json",
        "screening/screening-manifest.json",
        "evaluation/evaluation-manifest.json",
    ):
        path = root / relative
        if not path.is_file():
            continue
        manifest = read_json(path)
        state["stage_state"][relative] = {
            key: manifest.get(key)
            for key in (
                "status",
                "stage",
                "active_stage",
                "error_type",
                "completed_attempt_count",
                "owned_processes_stopped",
                "server_stopped",
                "samplers_stopped",
                "sampler_stopped",
                "completed_invocations",
                "failed_invocations",
                "summary",
            )
            if key in manifest
        }
        failure = manifest.get("failure")
        if isinstance(failure, dict):
            state["stage_state"][relative]["failure"] = {
                key: failure.get(key) for key in ("stage", "error_type")
            }
    summary_path = root / "baseline/baseline-summary.json"
    if summary_path.is_file():
        summary = read_json(summary_path)
        state["baseline_quality"] = summary["quality"]
        state["native"] = summary["is_arm64"] and not summary["synthetic_fixture"]
        state["baseline_process"] = summary["process"]
    hardware_path = root / "hardware/hardware.json"
    if hardware_path.is_file():
        state["hardware"] = read_json(hardware_path)
    command_path = root / "baseline/server-command.json"
    if command_path.is_file():
        state["baseline_command"] = read_json(command_path)
    preflight_path = root / "preflight/preflight.json"
    if preflight_path.is_file():
        report = PreflightReport.model_validate_json(
            json.dumps(redactor.value(read_json(preflight_path)))
        )
        state["preflight_outcome"] = report.outcome
        write_report(report, public / "preflight")
    if (root / "screening").is_dir():
        collect_diagnostics(optimize_dir=root, evidence_dir=public, redactor=redactor)
    state = redactor.value(state)
    write_json(public / "failure-state.json", state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", choices=["rejection", "optimization"], required=True)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--expected-model-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        parser.error("Native Linux Arm64 is required; synthetic evidence is not accepted")
    if hash_file_streaming(args.model) != args.expected_model_sha256:
        parser.error("Model SHA-256 differs from the pinned experiment")
    if args.output_dir.exists() or args.evidence_dir.exists():
        parser.error("Use new private and public output directories")
    args.evidence_dir.mkdir(parents=True)
    mode = (
        ResponseFormatMode.NONE
        if args.experiment == "rejection"
        else ResponseFormatMode.JSON_SCHEMA
    )
    workload = (
        ROOT
        / "workloads"
        / ("smoke-test.jsonl" if args.experiment == "rejection" else "support-triage.jsonl")
    )
    redactor = Redactor.from_paths(
        runner_temp=args.output_dir.parent,
        llama_build_dir=args.server.parent.parent,
        llama_source_dir=args.server.parent.parent.parent / "llama.cpp",
        model_path=args.model,
        model_dir=args.model.parent,
        optimize_dir=args.output_dir,
    )
    redactor = Redactor(
        tuple(
            sorted(
                [*redactor.replacements, (str(ROOT), "$WORKSPACE")],
                key=lambda item: len(item[0]),
                reverse=True,
            )
        )
    )
    evidence: dict[str, Any] = {
        "experiment": args.experiment,
        "synthetic": False,
        "model_sha256": args.expected_model_sha256,
        "workload_sha256": hash_file_streaming(workload),
        "quality_policy_sha256": load_quality_policy(None).sha256,
        "server_sha256": hash_file_streaming(args.server),
        "bench_sha256": hash_file_streaming(args.bench),
        "baseline_context_size": 2048 if args.experiment == "optimization" else None,
        "baseline_parallel_slots": 1 if args.experiment == "optimization" else None,
        "runs": [],
        "errors": [],
        "limitations": [
            "Ephemeral shared native Arm runner; not a dedicated production benchmark.",
            "Repeated searches use the same runner and are not independent hardware samples.",
            "Public evidence is a response-free projection; "
            "source hashes refer to original private artifacts.",
        ],
    }
    current_root: Path | None = None
    try:
        if args.experiment == "optimization":
            evidence["mode_probes"] = probe_output_modes(args.server, args.model, workload)
            if not evidence["mode_probes"]["unsupported"]["rejected"]:
                evidence["errors"].append("Runtime did not reject an unknown response format")
        repeats = 1 if args.experiment == "rejection" else args.repeats
        for repetition in range(1, repeats + 1):
            print(f"Starting {args.experiment} run {repetition}/{repeats}", flush=True)
            root = args.output_dir / f"run-{repetition}"
            current_root = root
            config = OptimizeConfig(
                server_binary=args.server,
                bench_binary=args.bench,
                model=args.model,
                workload=workload,
                output_dir=root,
                goal=OptimizationGoal.LATENCY,
                response_format_mode=mode,
                context_size=2048 if args.experiment == "optimization" else None,
                parallel_slots=1 if args.experiment == "optimization" else None,
                baseline_repetitions=2,
                evaluation_repetitions=2,
                screening_scenarios=ROOT / "configs/native-product-screening.yaml",
                advance_count=2,
                max_profiles=4,
                screening_repetitions=3,
                request_timeout_seconds=90.0,
                startup_timeout_seconds=120.0,
                maximum_total_duration_seconds=2700.0,
            )
            run = run_optimization(config)
            result = summarize_run(root, redactor, args.evidence_dir / f"run-{repetition}")
            result.update({"outcome": run.outcome, "exit_code": run.exit_code})
            result["request_audit"] = audit_requests(root, workload, mode)
            errors = assess_experiment(args.experiment, result, root)
            evidence["runs"].append(result)
            evidence["errors"].extend(errors)
            write_json(args.evidence_dir / "experiment.json", evidence)
            if errors:
                break
        if len(evidence["runs"]) > 1:
            evidence["repeatability"] = compare_repeats(evidence["runs"])
    except KeyboardInterrupt:
        evidence["errors"].append("Experiment interrupted")
    except Exception as exc:
        # Exception messages may contain model text or runtime logs.
        evidence["errors"].append(f"Experiment failed: {type(exc).__name__}")
        if current_root is not None:
            try:
                evidence["failure_state"] = capture_failure(
                    current_root, args.evidence_dir / "failure", redactor, exc
                )
            except Exception as capture_error:
                evidence["errors"].append(f"Failure capture failed: {type(capture_error).__name__}")
    finally:
        expected_runs = 1 if args.experiment == "rejection" else args.repeats
        evidence["passed"] = len(evidence["runs"]) == expected_runs and not evidence["errors"]
        write_json(args.evidence_dir / "experiment.json", redactor.value(evidence))
    print(
        json.dumps(
            {
                "experiment": args.experiment,
                "passed": evidence["passed"],
                "errors": evidence["errors"],
            }
        )
    )
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":

    def interrupt(_signal: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
