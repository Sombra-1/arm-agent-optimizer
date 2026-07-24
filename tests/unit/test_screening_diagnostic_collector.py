from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from aarchtune.screening.models import ScreeningConfig, ScreeningStatus
from aarchtune.screening.runner import run_screening


def _load_collector() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/collect_screening_diagnostics.py"
    spec = importlib.util.spec_from_file_location("collect_screening_diagnostics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


collector = _load_collector()
EXPECTED_FILES = collector.EXPECTED_FILES
Redactor = collector.Redactor
collect_diagnostics = collector.collect_diagnostics
redact_tree = collector.redact_tree


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values),
        encoding="utf-8",
    )


def _redactor(root: Path) -> Redactor:
    runner_temp = root / "runner-temp"
    return Redactor.from_paths(
        runner_temp=runner_temp,
        llama_build_dir=runner_temp / "llama.cpp-build",
        llama_source_dir=runner_temp / "llama.cpp",
        model_path=runner_temp / "aarchtune-model/model.gguf",
        model_dir=runner_temp / "aarchtune-model",
        optimize_dir=runner_temp / "optimization",
    )


def _screening_fixture(root: Path) -> tuple[Path, Path]:
    runner_temp = root / "runner-temp"
    optimize = runner_temp / "optimization"
    screening = optimize / "screening"
    evidence = runner_temp / "evidence"
    logs = screening / "logs/invocation"
    logs.mkdir(parents=True)
    (logs / "stdout.jsonl").write_text('{"raw_response":"not public"}\n', encoding="utf-8")
    (logs / "stderr.log").write_text("raw benchmark error\n", encoding="utf-8")
    (logs / "process-samples.jsonl").write_text("{}\n", encoding="utf-8")

    build = runner_temp / "llama.cpp-build/bin/llama-bench"
    model = runner_temp / "aarchtune-model/model.gguf"
    manifest = {
        "screening_id": "screening-test",
        "status": "failed",
        "stage": "failed",
        "output_directory": str(screening),
        "completed_invocations": 2,
        "failed_invocations": 1,
        "normalized_results": 1,
        "owned_processes_stopped": True,
        "samplers_stopped": True,
        "error_type": None,
        "error_message": f"failure under {screening}",
    }
    summary = {
        "screening_id": "screening-test",
        "status": "failed",
        "plan_profiles": 2,
        "bench_signatures": 2,
        "scenarios": 1,
        "expected_invocations": 2,
        "completed_invocations": 2,
        "failed_invocations": 1,
        "successful_signatures": 1,
        "partial_signatures": 0,
        "failed_signatures": 1,
        "advanced_candidates": 0,
    }
    inspection = {
        "binary_path": str(build),
        "mappings": {"mmap": {"boolean_form": "numeric_01"}},
    }
    signatures = [
        {"id": "signature-true", "settings": {"mmap": True}},
        {"id": "signature-false", "settings": {"mmap": False}},
    ]
    matrix = [
        {
            "invocation_id": "inv-true",
            "signature_id": "signature-true",
            "scenario_id": "decode",
            "repetition": 1,
            "command": {
                "arguments": [
                    str(build),
                    "--model",
                    str(model),
                    "--mmap",
                    "1",
                    "--jsonl",
                ],
                "output_format": "jsonl",
            },
        },
        {
            "invocation_id": "inv-false",
            "signature_id": "signature-false",
            "scenario_id": "decode",
            "repetition": 1,
            "command": {
                "arguments": [
                    str(build),
                    "--model",
                    str(model),
                    "--mmap",
                    "0",
                    "--jsonl",
                ],
                "output_format": "jsonl",
            },
        },
    ]
    executions = [
        {
            "invocation_id": "inv-true",
            "exit_code": 0,
            "timed_out": False,
            "stdout_path": str(logs / "stdout.jsonl"),
            "stderr_path": str(logs / "stderr.log"),
            "process_samples_path": str(logs / "process-samples.jsonl"),
        },
        {
            "invocation_id": "inv-false",
            "exit_code": 7,
            "timed_out": False,
            "stdout_path": str(logs / "stdout.jsonl"),
            "stderr_path": str(logs / "stderr.log"),
            "process_samples_path": str(logs / "process-samples.jsonl"),
        },
    ]
    failures = [
        {
            "invocation_id": "inv-false",
            "code": "nonzero_exit",
            "reason": f"exit_code=7 at {logs / 'stderr.log'}",
        }
    ]
    signature_results = [
        {"signature_id": "signature-true", "status": "completed"},
        {"signature_id": "signature-false", "status": "failed"},
    ]

    _write_json(screening / "screening-manifest.json", manifest)
    _write_json(screening / "screening-summary.json", summary)
    _write_json(
        screening / "screening-config.json",
        {"output_dir": str(screening), "bench_binary": str(build), "model": str(model)},
    )
    _write_json(screening / "llama-bench-inspection.json", inspection)
    _write_json(screening / "scenarios.json", {"path": str(runner_temp / "scenarios.yaml")})
    _write_jsonl(screening / "bench-signatures.jsonl", signatures)
    _write_jsonl(screening / "signature-membership.jsonl", [])
    _write_jsonl(screening / "benchmark-matrix.jsonl", matrix)
    _write_jsonl(screening / "raw-executions.jsonl", executions)
    _write_jsonl(screening / "process-summaries.jsonl", [])
    _write_jsonl(screening / "failures.jsonl", failures)
    _write_jsonl(screening / "signature-results.jsonl", signature_results)
    _write_jsonl(screening / "advancement-decisions.jsonl", [])
    _write_jsonl(screening / "advanced-candidates.jsonl", [])
    _write_jsonl(screening / "non-advanced-candidates.jsonl", [])
    return optimize, evidence


def test_collector_preserves_diagnostic_metadata_and_excludes_raw_files(
    tmp_path: Path,
) -> None:
    optimize, evidence = _screening_fixture(tmp_path)
    destination = collect_diagnostics(
        optimize_dir=optimize,
        evidence_dir=evidence,
        redactor=_redactor(tmp_path),
    )

    commands = [
        json.loads(line)
        for line in (destination / "command-evidence.jsonl").read_text().splitlines()
    ]
    assert commands[0]["argv"][:5] == [
        "$LLAMA_BUILD_DIR/bin/llama-bench",
        "--model",
        "$MODEL_PATH",
        "--mmap",
        "1",
    ]
    assert commands[0]["semantic_mmap"] is True
    assert commands[1]["argv"][3:5] == ["--mmap", "0"]
    assert commands[1]["semantic_mmap"] is False

    summary = json.loads((destination / "diagnostic-summary.json").read_text())
    assert summary["failure_codes"]["nonzero_exit"] == 1
    assert summary["execution_exit_codes"] == {"0": 1, "7": 1}
    assert summary["signature_statuses"]["completed"] == 1
    assert summary["signature_statuses"]["failed"] == 1
    assert summary["mmap_command_forms"]["numeric_true"] == 1
    assert summary["mmap_command_forms"]["numeric_false"] == 1

    samples = json.loads((destination / "failure-samples.json").read_text())
    assert samples == [
        {
            "code": "nonzero_exit",
            "exit_code": 7,
            "invocation_id": "inv-false",
            "reason": "exit_code=7 at $OPTIMIZE_DIR/screening/logs/invocation/stderr.log",
            "repetition": 1,
            "scenario_id": "decode",
            "signature_id": "signature-false",
            "timed_out": False,
        }
    ]

    inventory = json.loads((destination / "inventory.json").read_text())
    missing = inventory["files"]["normalized-measurements.jsonl"]
    assert missing["source_exists"] is False
    assert missing["included"] is False
    assert missing["exclusion_reason"] == "source_missing"
    assert inventory["raw_logs_included"] is False
    assert inventory["process_samples_included"] is False
    assert not (destination / "logs").exists()
    assert not list(destination.rglob("stdout.*"))
    assert not list(destination.rglob("stderr.log"))
    assert not list(destination.rglob("process-samples.jsonl"))

    public_text = "\n".join(
        path.read_text(encoding="utf-8") for path in destination.rglob("*") if path.is_file()
    )
    assert str(tmp_path) not in public_text
    assert "/home/runner/" not in public_text
    assert "/opt/hostedtoolcache/" not in public_text
    assert "raw_response" not in public_text


def test_collector_inventory_handles_absent_screening_stage(tmp_path: Path) -> None:
    runner_temp = tmp_path / "runner-temp"
    optimize = runner_temp / "optimization"
    evidence = runner_temp / "evidence"
    destination = collect_diagnostics(
        optimize_dir=optimize,
        evidence_dir=evidence,
        redactor=_redactor(tmp_path),
    )

    inventory = json.loads((destination / "inventory.json").read_text())
    assert inventory["screening_source_available"] is False
    assert set(inventory["files"]) == set(EXPECTED_FILES)
    assert all(not value["source_exists"] for value in inventory["files"].values())
    summary = json.loads((destination / "diagnostic-summary.json").read_text())
    assert summary["status"] is None
    assert all(value is None for value in summary["failure_codes"].values())
    assert all(value is None for value in summary["mmap_command_forms"].values())


def test_redact_tree_removes_runner_paths_and_temporary_uuid(tmp_path: Path) -> None:
    root = tmp_path / "public"
    root.mkdir()
    evidence = root / "paths.txt"
    evidence.write_text(
        "/home/runner/work/_temp/llama.cpp-build/bin/llama-bench\n"
        "/opt/hostedtoolcache/Python/3.12.0/arm64/bin/python\n"
        "58a4f788-b6aa-4e41-8cb7-8158652befb0\n",
        encoding="utf-8",
    )
    redact_tree(root, _redactor(tmp_path))
    content = evidence.read_text(encoding="utf-8")
    assert "/home/runner/" not in content
    assert "/opt/hostedtoolcache/" not in content
    assert "58a4f788-b6aa-4e41-8cb7-8158652befb0" not in content
    assert "$RUNNER_HOME/work/_temp/llama.cpp-build/bin/llama-bench" in content
    assert "$HOSTED_TOOLCACHE/Python/3.12.0/arm64/bin/python" in content
    assert "$TEMP_UUID" in content


def test_collector_reads_real_failed_synthetic_screening_artifacts(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "unstable")
    runner_temp = tmp_path / "runner-temp"
    optimize = runner_temp / "optimization"
    plan = runner_temp / "plan"
    shutil.copytree(screen_plan_dir, plan)
    scenario = runner_temp / "screening-scenarios.yaml"
    scenario.parent.mkdir(parents=True, exist_ok=True)
    scenario.write_text(
        "schema_version: '1.0'\n"
        "scenarios:\n"
        "  - {id: decode, prompt_tokens: 0, generation_tokens: 16}\n",
        encoding="utf-8",
    )
    result = run_screening(
        ScreeningConfig(
            plan_dir=plan,
            bench_binary=fake_bench,
            output_dir=optimize / "screening",
            scenario_path=scenario,
            advance_count=4,
            repetitions=3,
            invocation_timeout_seconds=2.0,
            total_timeout_seconds=120.0,
            sample_interval_seconds=0.05,
            allow_synthetic=True,
        )
    )
    assert result.status is ScreeningStatus.FAILED

    destination = collect_diagnostics(
        optimize_dir=optimize,
        evidence_dir=runner_temp / "evidence",
        redactor=_redactor(tmp_path),
    )
    summary = json.loads((destination / "diagnostic-summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["advanced_candidates"] == 0
    assert summary["signature_statuses"]["unstable"] > 0
    assert summary["mmap_command_forms"]["numeric_true"] > 0
    assert summary["mmap_command_forms"]["numeric_false"] > 0
    inventory = json.loads((destination / "inventory.json").read_text())
    assert all(item["included"] for item in inventory["files"].values())
