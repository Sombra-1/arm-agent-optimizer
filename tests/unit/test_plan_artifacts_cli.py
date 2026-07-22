from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aarchtune.baseline.errors import BaselineInputError
from aarchtune.cli import app
from aarchtune.optimization.artifacts import validate_plan_directory
from aarchtune.optimization.models import OptimizationGoal
from aarchtune.optimization.planner import create_search_plan

runner = CliRunner()


def _workload() -> Path:
    return Path(__file__).resolve().parents[2] / "workloads/smoke-test.jsonl"


def _arguments(fake_binary: Path, fake_model: Path, output: Path) -> list[str]:
    return [
        "plan",
        "--binary",
        str(fake_binary),
        "--model",
        str(fake_model),
        "--workload",
        str(_workload()),
        "--goal",
        "balanced",
        "--output-dir",
        str(output),
    ]


def test_explicit_plan_writes_round_trip_artifacts_without_benchmark_files(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    plan, output = create_search_plan(
        goal=OptimizationGoal.BALANCED,
        output_dir=tmp_path / "plan",
        binary=fake_binary,
        model=fake_model,
        workload=_workload(),
        maximum_profiles=8,
    )
    assert plan.summary.compatible_profiles == 8
    assert plan.candidates[0].id == "baseline"
    assert validate_plan_directory(output).valid is True
    assert len((output / "candidates.jsonl").read_text().splitlines()) == 8
    for candidate in plan.candidates:
        loaded = yaml.safe_load((output / "profiles" / f"{candidate.id}.yaml").read_text())
        assert loaded["profile_hash"] == candidate.profile_hash
    forbidden = {
        "raw-attempts.jsonl",
        "request-metrics.jsonl",
        "process-samples.jsonl",
        "quality-summary.json",
        "baseline-summary.json",
    }
    assert not forbidden.intersection(item.name for item in output.iterdir())


def test_tampered_profile_hash_is_detected(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    plan, output = create_search_plan(
        goal=OptimizationGoal.LATENCY,
        output_dir=tmp_path / "tamper",
        binary=fake_binary,
        model=fake_model,
        workload=_workload(),
    )
    path = output / "profiles" / f"{plan.candidates[0].id}.yaml"
    data = yaml.safe_load(path.read_text())
    data["profile_hash"] = "0" * 64
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    result = validate_plan_directory(output)
    assert result.valid is False
    assert any("YAML" in error or "hash" in error for error in result.errors)


def test_duplicate_candidate_and_forbidden_benchmark_are_detected(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    _, output = create_search_plan(
        goal=OptimizationGoal.MEMORY,
        output_dir=tmp_path / "duplicate",
        binary=fake_binary,
        model=fake_model,
        workload=_workload(),
    )
    plan_path = output / "search-plan.json"
    data = json.loads(plan_path.read_text())
    data["candidates"].append(data["candidates"][0])
    plan_path.write_text(json.dumps(data), encoding="utf-8")
    (output / "raw-attempts.jsonl").write_text("", encoding="utf-8")
    result = validate_plan_directory(output)
    assert result.valid is False
    assert any("unique" in error or "duplicated" in error for error in result.errors)
    assert any("Forbidden benchmark" in error for error in result.errors)


def test_tampered_companion_artifact_is_detected(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    _, output = create_search_plan(
        goal=OptimizationGoal.BALANCED,
        output_dir=tmp_path / "companion-tamper",
        binary=fake_binary,
        model=fake_model,
        workload=_workload(),
    )
    path = output / "hardware-fingerprint.json"
    data = json.loads(path.read_text())
    data["logical_cores"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    result = validate_plan_directory(output)
    assert result.valid is False
    assert any("hardware-fingerprint.json" in error for error in result.errors)


def test_missing_artifact_is_detected(tmp_path: Path) -> None:
    output = tmp_path / "missing"
    output.mkdir()
    result = validate_plan_directory(output)
    assert result.valid is False
    assert any("Missing required artifact" in error for error in result.errors)


def test_existing_output_is_protected_and_safe_overwrite_works(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    output = tmp_path / "protected"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(BaselineInputError, match="not empty"):
        create_search_plan(
            goal=OptimizationGoal.BALANCED,
            output_dir=output,
            binary=fake_binary,
            model=fake_model,
            workload=_workload(),
        )
    assert marker.exists()
    plan, _ = create_search_plan(
        goal=OptimizationGoal.BALANCED,
        output_dir=output,
        binary=fake_binary,
        model=fake_model,
        workload=_workload(),
        overwrite=True,
    )
    assert plan.candidates
    assert not marker.exists()


def test_dangerous_output_directory_is_rejected(fake_binary: Path, fake_model: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    with pytest.raises(BaselineInputError, match="dangerous output directory"):
        create_search_plan(
            goal=OptimizationGoal.BALANCED,
            output_dir=repository_root,
            binary=fake_binary,
            model=fake_model,
            workload=_workload(),
        )


def test_cli_human_json_maximum_and_validation(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    output = tmp_path / "human"
    result = runner.invoke(
        app, [*_arguments(fake_binary, fake_model, output), "--max-profiles", "7"]
    )
    assert result.exit_code == 0, result.output
    assert "AArchTune Search Plan Created" in result.output
    assert "Profiles compatible:    7" in result.output
    assert "No candidates were executed." in result.output
    assert "No performance conclusions were produced." in result.output
    validated = runner.invoke(app, ["plan", "validate", str(output)])
    assert validated.exit_code == 0, validated.output
    assert "Search plan valid" in validated.output

    json_output = tmp_path / "json"
    json_result = runner.invoke(app, [*_arguments(fake_binary, fake_model, json_output), "--json"])
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["summary"]["candidates_executed"] is False


def test_cli_invalid_goal_search_space_and_output_protection(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    invalid_goal = runner.invoke(
        app,
        [
            *_arguments(fake_binary, fake_model, tmp_path / "goal")[:-4],
            "--goal",
            "fastest",
            "--output-dir",
            str(tmp_path / "goal"),
        ],
    )
    assert invalid_goal.exit_code != 0
    bad_space = tmp_path / "bad.yaml"
    bad_space.write_text("schema_version: '1.0'\nunknown: true\n", encoding="utf-8")
    bad = runner.invoke(
        app,
        [
            *_arguments(fake_binary, fake_model, tmp_path / "bad"),
            "--search-space",
            str(bad_space),
        ],
    )
    assert bad.exit_code == 1
    assert "Error:" in bad.output


def test_cli_validate_tampered_plan_exits_one(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    output = tmp_path / "invalid"
    result = runner.invoke(app, _arguments(fake_binary, fake_model, output))
    assert result.exit_code == 0
    plan_path = output / "search-plan.json"
    data = json.loads(plan_path.read_text())
    data["goal"] = "latency"
    plan_path.write_text(json.dumps(data), encoding="utf-8")
    validation = runner.invoke(app, ["plan", "validate", str(output), "--json"])
    assert validation.exit_code == 1
    assert json.loads(validation.output)["valid"] is False
