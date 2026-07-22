from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from aarchtune.cli import app
from aarchtune.optimization.models import OptimizationGoal
from aarchtune.orchestration.config import configuration_hash
from aarchtune.orchestration.errors import ResumeError, StageError
from aarchtune.orchestration.models import (
    OptimizeConfig,
    OptimizeRunResult,
    OptimizeStage,
    OptimizeStageStatus,
    StageReference,
)
from aarchtune.orchestration.runner import run_optimization
from aarchtune.orchestration.stages import validate_baseline
from aarchtune.orchestration.validation import validate_optimization

REPOSITORY = Path(__file__).resolve().parents[2]
cli = CliRunner()


def _config(output: Path, **updates: object) -> OptimizeConfig:
    values: dict[str, object] = {
        "server_binary": REPOSITORY / "tests/fixtures/bin/fake-llama-server",
        "bench_binary": REPOSITORY / "tests/fixtures/bin/fake-llama-bench",
        "model": REPOSITORY / "tests/fixtures/models/fake-model.gguf",
        "workload": REPOSITORY / "workloads/smoke-test.jsonl",
        "goal": OptimizationGoal.BALANCED,
        "output_dir": output,
        "baseline_repetitions": 2,
        "evaluation_repetitions": 2,
        "warmup_requests": 1,
        "advance_count": 3,
        "max_profiles": 4,
        "screening_repetitions": 1,
        "request_timeout_seconds": 0.3,
        "startup_timeout_seconds": 2.0,
        "sample_interval_seconds": 0.05,
        "maximum_total_duration_seconds": 120.0,
        "allow_synthetic": True,
        "allow_non_arm_development": True,
    }
    values.update(updates)
    return OptimizeConfig.model_validate(values)


@pytest.fixture(scope="module")
def optimized_root(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("optimize-product") / "run"
    old_server = os.environ.get("FAKE_LLAMA_SCENARIO")
    old_bench = os.environ.get("FAKE_LLAMA_BENCH_SCENARIO")
    os.environ["FAKE_LLAMA_SCENARIO"] = "profile-matrix"
    os.environ["FAKE_LLAMA_BENCH_SCENARIO"] = "healthy-jsonl"
    try:
        result = run_optimization(_config(root))
        assert result.exit_code == 0
        yield root
    finally:
        if old_server is None:
            os.environ.pop("FAKE_LLAMA_SCENARIO", None)
        else:
            os.environ["FAKE_LLAMA_SCENARIO"] = old_server
        if old_bench is None:
            os.environ.pop("FAKE_LLAMA_BENCH_SCENARIO", None)
        else:
            os.environ["FAKE_LLAMA_BENCH_SCENARIO"] = old_bench


def test_one_command_stage_order_and_native_artifacts(optimized_root: Path) -> None:
    manifest = json.loads((optimized_root / "optimize-manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert [item["stage"] for item in manifest["stages"]] == [
        "doctor",
        "baseline",
        "planning",
        "screening",
        "evaluation",
        "finalization",
    ]
    assert all(item["validation_passed"] for item in manifest["stages"])
    assert (optimized_root / "baseline/manifest.json").is_file()
    assert (optimized_root / "plan/search-plan.json").is_file()
    assert (optimized_root / "screening/screening-manifest.json").is_file()
    assert (optimized_root / "evaluation/evaluation-manifest.json").is_file()
    assert (optimized_root / "final/bundle-manifest.json").is_file()


def test_complete_optimization_validates_and_has_cleanup_proof(optimized_root: Path) -> None:
    validation = validate_optimization(optimized_root)
    assert validation.valid, validation.errors
    manifest = json.loads((optimized_root / "optimize-manifest.json").read_text())
    assert manifest["owned_processes_stopped"] is True
    assert manifest["samplers_stopped"] is True
    assert validation.warnings == ["Synthetic test evidence — not Arm performance evidence"]


def test_optimization_validation_detects_missing_hash_and_cleanup(
    tmp_path: Path, optimized_root: Path
) -> None:
    missing = validate_optimization(tmp_path / "absent")
    assert not missing.valid
    assert any("manifest" in error.lower() for error in missing.errors)

    manifest_path = optimized_root / "optimize-manifest.json"
    original = manifest_path.read_text()
    manifest = json.loads(original)
    try:
        manifest["owned_processes_stopped"] = False
        manifest["stages"][0]["manifest_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        validation = validate_optimization(optimized_root)
        assert not validation.valid
        assert any("cleanup" in error.lower() for error in validation.errors)
        assert any("hash" in error.lower() for error in validation.errors)
    finally:
        manifest_path.write_text(original, encoding="utf-8")


def test_optimize_validation_cli_human_and_json(optimized_root: Path) -> None:
    human = cli.invoke(app, ["optimize", "validate", str(optimized_root)])
    machine = cli.invoke(app, ["optimize", "validate", str(optimized_root), "--json"])
    assert human.exit_code == 0
    assert "Optimization workflow valid" in human.output
    assert machine.exit_code == 0
    assert json.loads(machine.output)["valid"] is True


def test_optimize_cli_human_json_missing_and_error(
    tmp_path: Path, optimized_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aarchtune.orchestration import cli as optimize_cli

    existing = (
        json.loads((optimized_root / "bundle-manifest.json").read_text())
        if (optimized_root / "bundle-manifest.json").is_file()
        else json.loads((optimized_root / "final/bundle-manifest.json").read_text())
    )
    result = OptimizeRunResult(
        optimize_id="optimize-cli-fixture",
        output_dir=optimized_root,
        status=OptimizeStageStatus.COMPLETED,
        exit_code=0,
        outcome=existing["selection_outcome"],
        selected_profile_id=existing["selected_profile_id"],
        final_dir=optimized_root / "final",
        resumed=False,
        reused_stages=[],
    )
    monkeypatch.setattr(optimize_cli, "run_optimization", lambda config: result)
    base = [
        "optimize",
        "--server-binary",
        str(REPOSITORY / "tests/fixtures/bin/fake-llama-server"),
        "--bench-binary",
        str(REPOSITORY / "tests/fixtures/bin/fake-llama-bench"),
        "--model",
        str(REPOSITORY / "tests/fixtures/models/fake-model.gguf"),
        "--workload",
        str(REPOSITORY / "workloads/smoke-test.jsonl"),
        "--output-dir",
        str(tmp_path / "unused"),
        "--allow-synthetic",
    ]
    human = cli.invoke(app, base)
    machine = cli.invoke(app, [*base, "--json"])
    assert human.exit_code == 0
    assert "Optimization Complete" in human.output
    assert "Synthetic test evidence" in human.output
    assert "Sequential service rate" in human.output
    assert json.loads(machine.output)["optimize_id"] == "optimize-cli-fixture"

    missing = cli.invoke(app, ["optimize", "--goal", "balanced"])
    assert missing.exit_code == 1
    assert "required options missing" in missing.output

    def fail(config: object) -> OptimizeRunResult:
        raise StageError("synthetic CLI failure")

    monkeypatch.setattr(optimize_cli, "run_optimization", fail)
    failed = cli.invoke(app, base)
    assert failed.exit_code == 1
    assert "synthetic CLI failure" in failed.output


def test_resume_reuses_every_valid_stage_without_reexecution(optimized_root: Path) -> None:
    primaries = [
        optimized_root / "baseline/manifest.json",
        optimized_root / "plan/search-plan.json",
        optimized_root / "screening/screening-manifest.json",
        optimized_root / "evaluation/evaluation-manifest.json",
        optimized_root / "final/bundle-manifest.json",
    ]
    before = {path: path.stat().st_mtime_ns for path in primaries}
    result = run_optimization(_config(optimized_root, resume=True))
    after = {path: path.stat().st_mtime_ns for path in primaries}
    assert result.resumed is True
    assert result.reused_stages == [
        OptimizeStage.DOCTOR,
        OptimizeStage.BASELINE,
        OptimizeStage.PLANNING,
        OptimizeStage.SCREENING,
        OptimizeStage.EVALUATION,
        OptimizeStage.FINALIZATION,
    ]
    assert before == after
    manifest = json.loads((optimized_root / "optimize-manifest.json").read_text())
    assert all(item["reused"] for item in manifest["stages"])
    assert len(manifest["resume_decisions"]) == 6


def test_resume_rejects_configuration_mismatch(optimized_root: Path) -> None:
    with pytest.raises(ResumeError, match="configuration changed"):
        run_optimization(_config(optimized_root, resume=True, warmup_requests=2))


def test_resume_rejects_tampered_completed_stage(tmp_path: Path, optimized_root: Path) -> None:
    import shutil

    copied = tmp_path / "tampered-resume"
    shutil.copytree(optimized_root, copied)
    resume_config = _config(copied, resume=True)
    manifest_path = copied / "optimize-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["configuration_hash"] = configuration_hash(resume_config)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = copied / "plan/search-plan.json"
    plan.write_text(plan.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ResumeError, match=r"(?i)failed validation|invalid|hash"):
        run_optimization(resume_config)


def test_non_arm_requires_explicit_development_opt_in(tmp_path: Path) -> None:
    config = _config(
        tmp_path / "non-arm",
        allow_synthetic=False,
        allow_non_arm_development=False,
    )
    with pytest.raises(StageError, match="AArch64"):
        run_optimization(config)


def test_interruption_preserves_manifest_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aarchtune.orchestration import runner

    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_baseline_stage", interrupt)
    result = run_optimization(_config(tmp_path / "interrupt"))
    assert result.exit_code == 3
    assert result.status is OptimizeStageStatus.INTERRUPTED
    manifest = json.loads((result.output_dir / "optimize-manifest.json").read_text())
    assert manifest["failure"]["error_type"] == "KeyboardInterrupt"
    assert manifest["owned_processes_stopped"] is True
    assert manifest["samplers_stopped"] is True
    assert (result.output_dir / "hardware/hardware.json").is_file()


@pytest.mark.parametrize(
    ("attribute", "expected_stage"),
    [
        ("run_baseline_stage", "baseline"),
        ("run_planning_stage", "planning"),
        ("run_screening_stage", "screening"),
        ("run_evaluation_stage", "evaluation"),
        ("run_finalization_stage", "finalization"),
    ],
)
def test_stage_failure_is_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    expected_stage: str,
) -> None:
    from aarchtune.orchestration import runner

    def fail(*args: object, **kwargs: object) -> None:
        raise StageError("synthetic stage failure")

    for prior in (
        "run_baseline_stage",
        "run_planning_stage",
        "run_screening_stage",
        "run_evaluation_stage",
        "run_finalization_stage",
    ):
        if prior == attribute:
            break
        monkeypatch.setattr(runner, prior, lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner,
        "_reference",
        lambda stage, root, reused: StageReference(
            stage=stage,
            status=OptimizeStageStatus.COMPLETED,
            path=stage.value,
            identity=None,
            manifest_sha256="0" * 64,
            reused=reused,
            validation_passed=True,
        ),
    )
    monkeypatch.setattr(runner, attribute, fail)
    output = tmp_path / expected_stage
    with pytest.raises(StageError, match="synthetic stage failure"):
        run_optimization(_config(output))
    manifest = json.loads((output / "optimize-manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["failure"]["stage"] == expected_stage


@pytest.mark.parametrize("mutation", ["status", "cleanup", "attempt_count"])
def test_baseline_validation_rejects_incomplete_evidence(
    tmp_path: Path, optimized_root: Path, mutation: str
) -> None:
    import shutil

    baseline = tmp_path / mutation
    shutil.copytree(optimized_root / "baseline", baseline)
    if mutation == "attempt_count":
        with (baseline / "raw-attempts.jsonl").open("a", encoding="utf-8") as target:
            target.write("{}\n")
    else:
        manifest_path = baseline / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if mutation == "status":
            manifest["status"] = "failed"
        else:
            manifest["server_stopped"] = False
        manifest_path.write_text(json.dumps(manifest))
    valid, reason = validate_baseline(baseline)
    assert not valid
    assert reason


def test_baseline_validation_rejects_missing_and_invalid_schema(tmp_path: Path) -> None:
    valid, reason = validate_baseline(tmp_path / "missing")
    assert not valid and reason and "Missing" in reason
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    for name in (
        "manifest.json",
        "hardware.json",
        "runtime-inspection.json",
        "server-command.json",
        "model.json",
        "workload.json",
        "raw-attempts.jsonl",
        "quality-summary.json",
        "baseline-summary.json",
    ):
        (invalid / name).write_text("{}")
    valid, reason = validate_baseline(invalid)
    assert not valid and reason and "schema" in reason


def test_stage_adapters_surface_native_execution_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aarchtune.orchestration import stages

    config = _config(tmp_path / "unused")
    monkeypatch.setattr(stages, "run_baseline", lambda config: SimpleNamespace(exit_code=2))
    with pytest.raises(StageError, match="Baseline execution failed"):
        stages.run_baseline_stage(config, tmp_path / "baseline-failure")

    monkeypatch.setattr(
        stages,
        "run_screening",
        lambda config: SimpleNamespace(exit_code=2, summary=None),
    )
    monkeypatch.setattr(
        stages,
        "validate_screening_directory",
        lambda path: SimpleNamespace(valid=False, errors=["invalid"]),
    )
    with pytest.raises(StageError, match="Screening execution failed"):
        stages.run_screening_stage(config, tmp_path / "screening-failure", tmp_path / "plan")

    monkeypatch.setattr(stages, "run_evaluation", lambda config: SimpleNamespace(exit_code=2))
    monkeypatch.setattr(
        stages,
        "validate_evaluation_directory",
        lambda path: SimpleNamespace(valid=False, errors=["invalid"]),
    )
    with pytest.raises(StageError, match="infrastructure failed"):
        stages.run_evaluation_stage(config, tmp_path / "evaluation-failure", tmp_path / "screening")

    monkeypatch.setattr(stages, "finalize_evaluation", lambda config: SimpleNamespace(exit_code=1))
    monkeypatch.setattr(
        stages,
        "validate_bundle",
        lambda path: SimpleNamespace(valid=False, errors=["invalid final bundle"]),
    )
    with pytest.raises(StageError, match="Final bundle validation failed"):
        stages.run_finalization_stage(
            config, tmp_path / "finalization-failure", tmp_path / "evaluation"
        )
