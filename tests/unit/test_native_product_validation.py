from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from aarchtune.optimization.models import OptimizationGoal
from aarchtune.orchestration.models import OptimizeConfig
from aarchtune.orchestration.runner import run_optimization
from aarchtune.runtime.config import ResponseFormatMode

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("native_product_validation")


@pytest.fixture
def synthetic_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", "profile-matrix")
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    config = OptimizeConfig(
        server_binary=ROOT / "tests/fixtures/bin/fake-llama-server",
        bench_binary=ROOT / "tests/fixtures/bin/fake-llama-bench",
        model=ROOT / "tests/fixtures/models/fake-model.gguf",
        workload=ROOT / "workloads/smoke-test.jsonl",
        output_dir=tmp_path / "run",
        goal=OptimizationGoal.LATENCY,
        max_profiles=4,
        advance_count=3,
        screening_repetitions=1,
        baseline_repetitions=2,
        evaluation_repetitions=2,
        allow_synthetic=True,
        maximum_total_duration_seconds=120.0,
    )
    result = run_optimization(config)
    assert result.exit_code == 0
    return config.output_dir


def test_public_projection_excludes_responses_and_rejects_synthetic_native_claims(
    native: ModuleType, synthetic_run: Path, tmp_path: Path
) -> None:
    redactor = native.Redactor(((str(tmp_path), "$TEMP"), (str(ROOT), "$WORKSPACE")))
    public = tmp_path / "public"
    result = native.summarize_run(synthetic_run, redactor, public)
    result["request_audit"] = native.audit_requests(
        synthetic_run, ROOT / "workloads/smoke-test.jsonl", ResponseFormatMode.NONE
    )
    assert result["validation"]["valid"] and result["final_validation"]["valid"]
    assert result["passport_validation"]["valid"]
    assert result["request_audit"]["passed"]
    assert result["native"] is False
    errors = native.assess_experiment("optimization", result, synthetic_run)
    assert errors == ["Native evidence validation or process cleanup failed"]
    for path in public.rglob("*"):
        if path.is_file():
            assert "response_text" not in path.read_text()
            assert str(tmp_path) not in path.read_text()
    assert (public / "report.html").is_file()


def test_request_audit_detects_changed_candidate_generation(
    native: ModuleType, synthetic_run: Path
) -> None:
    path = next((synthetic_run / "evaluation").rglob("request-metrics.jsonl"))
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[0]["request"]["temperature"] = 1.5
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    result = native.audit_requests(
        synthetic_run, ROOT / "workloads/smoke-test.jsonl", ResponseFormatMode.NONE
    )
    assert result["passed"] is False and "Generation contract changed" in result["errors"][0]


def test_rejection_acceptance_requires_no_costly_stage(native: ModuleType, tmp_path: Path) -> None:
    result = {
        "validation": {"valid": True},
        "native": True,
        "cleanup_confirmed": True,
        "request_audit": {"passed": True},
        "preflight_outcome": "quality_failed",
        "stages": ["doctor", "baseline"],
    }
    assert native.assess_experiment("rejection", result, tmp_path) == []
    (tmp_path / "screening").mkdir()
    assert native.assess_experiment("rejection", result, tmp_path) == [
        "Costly search stages ran after a failing baseline"
    ]


def test_repeatability_exposes_changed_quality_and_performance(native: ModuleType) -> None:
    runs = [
        {
            "candidates": [
                {
                    "hash": "same",
                    "baseline": True,
                    "quality_status": "passed",
                    "p95_latency_seconds": 1.0,
                }
            ]
        },
        {
            "candidates": [
                {
                    "hash": "same",
                    "baseline": True,
                    "quality_status": "failed",
                    "p95_latency_seconds": 1.4,
                }
            ]
        },
    ]
    result = native.compare_repeats(runs)
    assert result["quality_decisions_consistent_for_shared_candidates"] is False
    assert result["baseline_p95_relative_range"] == pytest.approx(0.4)


def test_failed_stage_capture_keeps_state_without_exception_or_response_text(
    native: ModuleType, synthetic_run: Path, tmp_path: Path
) -> None:
    path = synthetic_run / "optimize-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["status"] = "failed"
    manifest["failure"] = {
        "stage": "screening",
        "error_type": "StageError",
        "message": "PRIVATE_MESSAGE",
    }
    path.write_text(json.dumps(manifest))
    redactor = native.Redactor(((str(tmp_path), "$TEMP"), (str(ROOT), "$WORKSPACE")))
    result = native.capture_failure(
        synthetic_run, tmp_path / "public-failure", redactor, ValueError("PRIVATE_MESSAGE")
    )
    assert result["stage_state"]["optimize-manifest.json"]["failure"]["stage"] == "screening"
    assert result["preflight_outcome"] == "quality_passed"
    assert result["baseline_quality"]["task_attempt_success_rate"] == 1.0
    assert "PRIVATE_MESSAGE" not in json.dumps(result)
    assert "response_text" not in json.dumps(result)
    assert (tmp_path / "public-failure/screening-diagnostics/screening-summary.json").is_file()


def test_native_workflow_is_pinned_bounded_and_uses_a_local_environment() -> None:
    workflow_path = ROOT / ".github/workflows/native-arm64-product.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    job = workflow["jobs"]["experiment"]
    assert job["runs-on"] == "ubuntu-24.04-arm"
    assert job["timeout-minutes"] == 120
    assert job["strategy"]["fail-fast"] is False
    assert len(job["env"]["LLAMA_COMMIT"]) == 40
    assert "rejection" in job["strategy"]["matrix"]["experiment"]
    assert "optimization" in job["strategy"]["matrix"]["experiment"]
    assert len(job["env"]["MODEL_REVISION"]) == 40
    assert len(job["env"]["MODEL_SHA256"]) == 64
    assert "scripts/quality_precheck.py resolve-profile" in workflow_path.read_text()
    assert ".venv/bin/python -m pip install" in workflow_path.read_text()
    parent = yaml.safe_load((ROOT / ".github/workflows/real-arm64-smoke.yml").read_text())
    assert (
        parent["jobs"]["product-validation"]["uses"]
        == "./.github/workflows/native-arm64-product.yml"
    )
