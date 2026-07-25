from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from aarchtune.baseline.models import BaselineRunConfig
from aarchtune.baseline.runner import run_baseline

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-arm64-quality-precheck.yml"
POLICY = ROOT / "configs/default-quality-policy.yaml"


def _helper() -> ModuleType:
    path = ROOT / "scripts/quality_precheck.py"
    spec = importlib.util.spec_from_file_location("quality_precheck", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict[str, object]:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _baseline(
    tmp_path: Path,
    fake_binary: Path,
    fake_model: Path,
    *,
    scenario: str = "healthy-with-timings",
) -> Path:
    output = tmp_path / scenario
    result = run_baseline(
        BaselineRunConfig(
            binary_path=fake_binary,
            model_path=fake_model,
            workload_path=ROOT / "workloads/smoke-test.jsonl",
            output_dir=output,
            repetitions=2,
            warmup_requests=1,
            request_timeout_seconds=0.5,
            startup_timeout_seconds=2.0,
            shutdown_timeout_seconds=0.5,
            sample_interval_seconds=0.05,
            extra_environment={"FAKE_LLAMA_SCENARIO": scenario},
        )
    )
    assert result.exit_code == 0
    return output


def test_model_pin_is_immutable_and_exact() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["quality-precheck"]
    environment = job["env"]
    assert environment["MODEL_REPOSITORY"] == "Qwen/Qwen2.5-7B-Instruct-GGUF"
    assert environment["MODEL_REVISION"] == "293ca9a10157b0e5fc5cb32af8b636a88bede891"
    assert environment["MODEL_FILENAME"] == "qwen2.5-7b-instruct-q3_k_m.gguf"
    assert environment["MODEL_SIZE_BYTES"] == "3808391072"
    assert environment["MODEL_SHA256"] == (
        "a96b16179dc6cc9afdf0cf7a96a80c199cbd00b9be207c3465be21cb721cca5e"
    )
    assert f"/resolve/{environment['MODEL_REVISION']}/" in environment["MODEL_URL"]
    assert "/main/" not in environment["MODEL_URL"]
    assert "latest" not in environment["MODEL_URL"]


def test_model_size_and_sha_mismatches_fail(tmp_path: Path) -> None:
    helper = _helper()
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not a model")
    with pytest.raises(ValueError, match="size mismatch"):
        helper.verify_model(model, 1, "0" * 64)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        helper.verify_model(model, model.stat().st_size, "0" * 64)


def test_existing_policy_passes_complete_synthetic_baseline(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    helper = _helper()
    baseline = _baseline(tmp_path, fake_binary, fake_model)
    public = tmp_path / "public"
    outcome = helper.sanitize_baseline(baseline, POLICY, public)
    assert outcome == "quality_policy_passed"
    policy = json.loads((public / "quality-policy-outcome.json").read_text())
    assert policy["outcome"] == "quality_policy_passed"
    assert policy["aggregate"]["completed_attempts"] == 10
    assert policy["thresholds"]["absolute_minimums"]["task_success_rate"] == 0.95
    assert not any("response" in path.name for path in public.iterdir())
    serialized = " ".join(path.read_text() for path in public.iterdir())
    assert '"response_text"' not in serialized
    assert '"server_fields"' not in serialized
    assert '"observed"' not in serialized


def test_missing_attempt_is_incomplete_not_pass(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    helper = _helper()
    baseline = _baseline(tmp_path, fake_binary, fake_model)
    raw = baseline / "raw-attempts.jsonl"
    lines = raw.read_text(encoding="utf-8").splitlines()
    raw.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    public = tmp_path / "public"
    outcome = helper.sanitize_baseline(baseline, POLICY, public)
    report = json.loads((public / "quality-policy-outcome.json").read_text())
    assert outcome == "quality_evidence_incomplete"
    assert report["evidence_errors"]


def test_failed_validators_are_preserved_without_response_content(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    helper = _helper()
    baseline = _baseline(
        tmp_path,
        fake_binary,
        fake_model,
        scenario="mixed-task-quality",
    )
    public = tmp_path / "public"
    outcome = helper.sanitize_baseline(baseline, POLICY, public)
    classifications = json.loads(
        (public / "per-task-classifications.json").read_text(encoding="utf-8")
    )
    incident = next(item for item in classifications if item["task_id"] == "smoke-incident-001")
    assert outcome == "quality_policy_failed"
    assert all("exact_value" in item["failed_validators"] for item in incident["attempts"])
    assert all("wrong_allowed_value" in item["classifications"] for item in incident["attempts"])
    assert "unknown" not in json.dumps(classifications)


def test_workflow_is_baseline_only_and_cleanup_always_runs() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = _workflow()
    steps = workflow["jobs"]["quality-precheck"]["steps"]
    cleanup = next(
        item
        for item in steps
        if item.get("name") == "Clean up workflow-owned processes and sensitive evidence"
    )
    assert cleanup["if"] == "always()"
    assert "aarchtune baseline \\" in text
    assert "aarchtune optimize " not in text
    assert "aarchtune screen " not in text
    assert "aarchtune evaluate " not in text
    assert "aarchtune plan " not in text
    assert "llama-bench" not in text
    assert "pkill" not in text
    assert "killall" not in text
    assert "raw-attempts.jsonl" in text
    assert "retention-days: 14" in text


def test_public_binary_checksums_use_relative_names(tmp_path: Path) -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'cd "$LLAMA_BUILD_DIR/bin"' in text
    assert "sha256sum llama-server llama-cli" in text
    assert 'sha256sum "$path"' not in text

    binary_dir = tmp_path / "absolute" / "runner" / "build" / "bin"
    binary_dir.mkdir(parents=True)
    (binary_dir / "llama-server").write_bytes(b"server")
    (binary_dir / "llama-cli").write_bytes(b"cli")
    result = subprocess.run(
        [
            "bash",
            "-c",
            '(cd "$1" && sha256sum llama-server llama-cli)',
            "checksum-test",
            str(binary_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "/home/runner/" not in result.stdout
    assert "/opt/hostedtoolcache/" not in result.stdout
    assert str(tmp_path) not in result.stdout
    assert "llama-server" in result.stdout
    assert "llama-cli" in result.stdout
    assert len(result.stdout.splitlines()[0].split()[0]) == 64
    assert len(result.stdout.splitlines()[1].split()[0]) == 64


def _matches(pattern: str, text: str) -> bool:
    result = subprocess.run(
        ["grep", "-Eq", pattern],
        input=text,
        text=True,
        check=False,
    )
    return result.returncode == 0


def test_privacy_scan_allows_placeholders_and_rejects_values() -> None:
    environment = _workflow()["jobs"]["quality-precheck"]["env"]
    general = environment["PUBLIC_EVIDENCE_SCAN_PATTERN"]
    assignment = environment["HF_TOKEN_ASSIGNMENT_PATTERN"]
    placeholders = [
        "value from HF_TOKEN environment variable",
        "--hf-token <token>",
        "HF_TOKEN=<token>",
        '"HF_TOKEN": "<token>"',
    ]
    assert all(not _matches(general, value) for value in placeholders)
    assert all(not _matches(assignment, value) for value in placeholders)
    assert _matches(assignment, "HF_TOKEN=actual-value")
    assert _matches(assignment, '"HF_TOKEN": "actual-value"')
    assert _matches(general, "Authorization: Bearer actual-value")
