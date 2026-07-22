from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aarchtune.baseline.errors import BaselineInputError
from aarchtune.cli import app
from aarchtune.screening.capabilities import clear_bench_capability_cache
from aarchtune.screening.errors import BenchDiscoveryError, ScreeningError
from aarchtune.screening.models import ScreeningConfig, ScreeningStatus
from aarchtune.screening.runner import run_screening
from aarchtune.screening.validation import validate_screening_directory

cli = CliRunner()


def _scenario(path: Path, *, prompt: int = 0, generation: int = 16) -> Path:
    path.write_text(
        "schema_version: '1.0'\nscenarios:\n"
        f"  - {{id: probe, prompt_tokens: {prompt}, generation_tokens: {generation}}}\n",
        encoding="utf-8",
    )
    return path


def _config(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    *,
    name: str,
    repetitions: int = 2,
    prompt: int = 0,
) -> ScreeningConfig:
    return ScreeningConfig(
        plan_dir=screen_plan_dir,
        bench_binary=fake_bench,
        output_dir=tmp_path / name,
        scenario_path=_scenario(tmp_path / f"{name}.yaml", prompt=prompt),
        repetitions=repetitions,
        advance_count=4,
        invocation_timeout_seconds=2.0,
        total_timeout_seconds=120.0,
        sample_interval_seconds=0.05,
        allow_synthetic=True,
    )


def test_healthy_screening_artifacts_and_validation(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    result = run_screening(_config(tmp_path, screen_plan_dir, fake_bench, name="healthy"))
    assert result.status is ScreeningStatus.COMPLETED
    assert result.exit_code == 0
    assert result.summary is not None
    assert result.summary.advanced_candidates == 4
    assert result.summary.quality_evaluated is False
    assert result.summary.final_candidate_selected is False
    validation = validate_screening_directory(result.output_dir)
    assert validation.valid is True, validation.errors
    required = {
        "screening-manifest.json",
        "raw-executions.jsonl",
        "normalized-measurements.jsonl",
        "advancement-decisions.jsonl",
        "advanced-candidates.jsonl",
    }
    assert required <= {item.name for item in result.output_dir.iterdir()}
    assert not {
        "best-profile.yaml",
        "run-optimized.sh",
        "optimization-passport.json",
        "report.html",
    }.intersection(item.name for item in result.output_dir.iterdir())


@pytest.mark.parametrize("scenario", ["healthy-json", "healthy-csv"])
def test_screening_uses_other_inspected_machine_formats(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", scenario)
    clear_bench_capability_cache()
    result = run_screening(
        _config(tmp_path, screen_plan_dir, fake_bench, name=scenario, repetitions=1)
    )
    assert result.status is ScreeningStatus.COMPLETED
    inspection = json.loads((result.output_dir / "llama-bench-inspection.json").read_text())
    assert inspection["output"]["selected_format"] == scenario.removeprefix("healthy-")


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_exit"),
    [
        ("missing-throughput", ScreeningStatus.FAILED, 2),
        ("unstable", ScreeningStatus.FAILED, 2),
        ("partial-failure", ScreeningStatus.PARTIAL, 3),
    ],
)
def test_failure_stability_and_partial_statuses(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_status: ScreeningStatus,
    expected_exit: int,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", scenario)
    repetitions = 3 if scenario == "unstable" else 2
    prompt = 512 if scenario == "partial-failure" else 0
    result = run_screening(
        _config(
            tmp_path,
            screen_plan_dir,
            fake_bench,
            name=scenario,
            repetitions=repetitions,
            prompt=prompt,
        )
    )
    assert result.status is expected_status
    assert result.exit_code == expected_exit
    assert result.summary is not None
    if scenario == "partial-failure":
        assert result.summary.advanced_candidates > 0
        assert result.summary.failed_invocations > 0
    else:
        assert result.summary.advanced_candidates == 0


def test_synthetic_rejection_missing_bench_and_invalid_plan(
    tmp_path: Path, screen_plan_dir: Path, fake_bench: Path
) -> None:
    config = _config(tmp_path, screen_plan_dir, fake_bench, name="reject").model_copy(
        update={"allow_synthetic": False}
    )
    with pytest.raises(ScreeningError, match="allow-synthetic"):
        run_screening(config)
    with pytest.raises(BenchDiscoveryError):
        run_screening(_config(tmp_path, screen_plan_dir, tmp_path / "missing", name="missing"))
    broken = tmp_path / "broken-plan"
    broken.mkdir()
    with pytest.raises(ScreeningError, match="integrity"):
        run_screening(_config(tmp_path, broken, fake_bench, name="broken"))


def test_screening_rejects_workload_hardware_and_runtime_provenance_drift(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aarchtune.screening import runner as runner_module

    plan = json.loads((screen_plan_dir / "search-plan.json").read_text())
    workload_path = Path(plan["input"]["workload"]["path"]).resolve()
    original_hash = runner_module.hash_file_streaming

    def changed_workload(path: Path) -> str:
        return "0" * 64 if path.resolve() == workload_path else original_hash(path)

    monkeypatch.setattr(runner_module, "hash_file_streaming", changed_workload)
    with pytest.raises(ScreeningError, match="Workload provenance"):
        run_screening(_config(tmp_path, screen_plan_dir, fake_bench, name="workload-drift"))
    monkeypatch.setattr(runner_module, "hash_file_streaming", original_hash)

    original_detector = runner_module.detect_hardware

    def changed_hardware(*, model_path: Path | None = None) -> object:
        report = original_detector(model_path=model_path)
        return report.model_copy(update={"cpu_model": "synthetic changed CPU"})

    monkeypatch.setattr(runner_module, "detect_hardware", changed_hardware)
    with pytest.raises(ScreeningError, match="hardware fingerprint"):
        run_screening(_config(tmp_path, screen_plan_dir, fake_bench, name="hardware-drift"))
    monkeypatch.setattr(runner_module, "detect_hardware", original_detector)

    original_inspector = runner_module.inspect_llama_server_capabilities

    def changed_runtime(*args: object, **kwargs: object) -> object:
        capabilities = original_inspector(*args, **kwargs)
        return capabilities.model_copy(update={"version": "synthetic changed runtime"})

    monkeypatch.setattr(runner_module, "inspect_llama_server_capabilities", changed_runtime)
    with pytest.raises(ScreeningError, match="runtime version or capability"):
        run_screening(_config(tmp_path, screen_plan_dir, fake_bench, name="runtime-drift"))


def test_output_protection_and_safe_overwrite(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    config = _config(tmp_path, screen_plan_dir, fake_bench, name="protected", repetitions=1)
    config.output_dir.mkdir()
    marker = config.output_dir / "keep"
    marker.write_text("keep")
    with pytest.raises(BaselineInputError, match="not empty"):
        run_screening(config)
    result = run_screening(config.model_copy(update={"overwrite": True}))
    assert result.status is ScreeningStatus.COMPLETED
    assert not marker.exists()
    dangerous = config.model_copy(update={"output_dir": Path(__file__).resolve().parents[2]})
    with pytest.raises(BaselineInputError, match="dangerous"):
        run_screening(dangerous)


def test_validation_detects_signature_yaml_reference_and_forbidden_tampering(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    result = run_screening(
        _config(tmp_path, screen_plan_dir, fake_bench, name="tamper", repetitions=1)
    )
    signature_path = result.output_dir / "bench-signatures.jsonl"
    lines = signature_path.read_text().splitlines()
    first = json.loads(lines[0])
    first["signature_hash"] = "0" * 64
    lines[0] = json.dumps(first)
    signature_path.write_text("\n".join(lines) + "\n")
    (result.output_dir / "report.html").write_text("forbidden")
    validation = validate_screening_directory(result.output_dir)
    assert validation.valid is False
    assert any("signature hash" in error.lower() for error in validation.errors)
    assert any("Forbidden" in error for error in validation.errors)

    profile = next((result.output_dir / "advanced-profiles").glob("*.yaml"))
    data = yaml.safe_load(profile.read_text())
    data["profile_hash"] = "f" * 64
    profile.write_text(yaml.safe_dump(data, sort_keys=True))
    assert validate_screening_directory(result.output_dir).valid is False


def test_validation_detects_plan_candidate_raw_reference_and_duplicate_tampering(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    result = run_screening(
        _config(tmp_path, screen_plan_dir, fake_bench, name="tamper-source", repetitions=1)
    )

    plan_copy = tmp_path / "tamper-plan"
    shutil.copytree(result.output_dir, plan_copy)
    reference = plan_copy / "search-plan-reference.json"
    data = json.loads(reference.read_text())
    data["plan_hash"] = "0" * 64
    reference.write_text(json.dumps(data))
    assert any(
        "Search-plan hash" in error for error in validate_screening_directory(plan_copy).errors
    )

    candidate_copy = tmp_path / "tamper-candidate"
    shutil.copytree(result.output_dir, candidate_copy)
    decisions = candidate_copy / "advancement-decisions.jsonl"
    lines = decisions.read_text().splitlines()
    data = json.loads(lines[0])
    data["candidate_hash"] = "0" * 64
    lines[0] = json.dumps(data)
    decisions.write_text("\n".join(lines) + "\n")
    assert any(
        "Candidate hash" in error for error in validate_screening_directory(candidate_copy).errors
    )

    raw_copy = tmp_path / "tamper-raw"
    shutil.copytree(result.output_dir, raw_copy)
    execution = json.loads((raw_copy / "raw-executions.jsonl").read_text().splitlines()[0])
    Path(execution["stdout_path"]).unlink()
    assert any(
        "missing artifact" in error for error in validate_screening_directory(raw_copy).errors
    )

    duplicate_copy = tmp_path / "tamper-duplicate"
    shutil.copytree(result.output_dir, duplicate_copy)
    advanced = duplicate_copy / "advanced-candidates.jsonl"
    first = advanced.read_text().splitlines()[0]
    advanced.write_text(advanced.read_text() + first + "\n")
    assert any(
        "Duplicate advanced" in error
        for error in validate_screening_directory(duplicate_copy).errors
    )


def test_validation_detects_summary_matrix_and_membership_tampering(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    result = run_screening(
        _config(tmp_path, screen_plan_dir, fake_bench, name="tamper-cross-check", repetitions=1)
    )

    summary_copy = tmp_path / "tamper-summary"
    shutil.copytree(result.output_dir, summary_copy)
    summary_path = summary_copy / "screening-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["advanced_candidates"] -= 1
    summary_path.write_text(json.dumps(summary))
    assert any("summary" in error for error in validate_screening_directory(summary_copy).errors)

    matrix_copy = tmp_path / "tamper-matrix"
    shutil.copytree(result.output_dir, matrix_copy)
    matrix_path = matrix_copy / "benchmark-matrix.jsonl"
    matrix_lines = matrix_path.read_text().splitlines()
    matrix = json.loads(matrix_lines[0])
    matrix["signature_hash"] = "0" * 64
    matrix_lines[0] = json.dumps(matrix)
    matrix_path.write_text("\n".join(matrix_lines) + "\n")
    assert any(
        "matrix signature" in error.lower()
        for error in validate_screening_directory(matrix_copy).errors
    )

    membership_copy = tmp_path / "tamper-membership"
    shutil.copytree(result.output_dir, membership_copy)
    membership_path = membership_copy / "signature-membership.jsonl"
    membership_lines = membership_path.read_text().splitlines()
    membership = json.loads(membership_lines[0])
    membership["candidate_hash"] = "0" * 64
    membership_lines[0] = json.dumps(membership)
    membership_path.write_text("\n".join(membership_lines) + "\n")
    assert any(
        "signature membership" in error.lower()
        for error in validate_screening_directory(membership_copy).errors
    )


def test_simulated_interruption_updates_manifest_and_returns_three(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aarchtune.screening import runner as runner_module

    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")

    def interrupt(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise KeyboardInterrupt

    monkeypatch.setattr(runner_module, "run_one_shot", interrupt)
    result = run_screening(
        _config(tmp_path, screen_plan_dir, fake_bench, name="interrupted", repetitions=1)
    )
    assert result.status is ScreeningStatus.INTERRUPTED
    assert result.exit_code == 3
    manifest = json.loads((result.output_dir / "screening-manifest.json").read_text())
    assert manifest["status"] == "interrupted"
    assert manifest["owned_processes_stopped"] is True
    assert manifest["samplers_stopped"] is True


def test_cli_human_json_validation_and_partial_exit(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario_file = _scenario(tmp_path / "cli.yaml")
    base = [
        "screen",
        "--plan",
        str(screen_plan_dir),
        "--bench-binary",
        str(fake_bench),
        "--scenarios",
        str(scenario_file),
        "--repetitions",
        "1",
        "--allow-synthetic",
    ]
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    human_dir = tmp_path / "cli-human"
    human = cli.invoke(app, [*base, "--output-dir", str(human_dir)])
    assert human.exit_code == 0, human.output
    assert "Low-Level Screening Complete" in human.output
    assert "Synthetic low-level measurements" in human.output
    assert "Agent quality" in human.output
    assert "No final winner has been selected" in human.output
    validated = cli.invoke(app, ["screen", "validate", str(human_dir)])
    assert validated.exit_code == 0

    json_dir = tmp_path / "cli-json"
    machine = cli.invoke(app, [*base, "--output-dir", str(json_dir), "--json"])
    assert machine.exit_code == 0
    assert json.loads(machine.output)["summary"]["final_candidate_selected"] is False

    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "partial-failure")
    partial_scenarios = _scenario(tmp_path / "partial.yaml", prompt=512)
    partial = cli.invoke(
        app,
        [
            *base,
            "--scenarios",
            str(partial_scenarios),
            "--output-dir",
            str(tmp_path / "cli-partial"),
        ],
    )
    assert partial.exit_code == 3
