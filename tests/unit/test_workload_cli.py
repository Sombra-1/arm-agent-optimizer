from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aarchtune.cli import app

runner = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[2]
SMOKE = REPOSITORY / "workloads" / "smoke-test.jsonl"
PASSING = REPOSITORY / "tests" / "fixtures" / "responses" / "passing.jsonl"
MIXED = REPOSITORY / "tests" / "fixtures" / "responses" / "mixed.jsonl"


def test_workload_validate_success() -> None:
    result = runner.invoke(app, ["workload", "validate", str(SMOKE)])

    assert result.exit_code == 0
    assert "Workload valid" in result.stdout
    assert "Tasks" in result.stdout
    assert "5" in result.stdout
    assert "Deterministic" in result.stdout


def test_workload_validate_failure(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text("not json\n", encoding="utf-8")

    result = runner.invoke(app, ["workload", "validate", str(invalid)])

    assert result.exit_code == 1
    assert "Invalid JSON" in result.stderr


def test_workload_validate_json_output() -> None:
    result = runner.invoke(app, ["workload", "validate", str(SMOKE), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["tasks"] == 5
    assert payload["categories"] == 5
    assert payload["deterministic"] is True


def test_workload_validate_writes_output(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "summary.json"

    result = runner.invoke(
        app,
        ["workload", "validate", str(SMOKE), "--output", str(output)],
    )

    assert result.exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["validators"] == 25


def test_evaluation_pass_exit_code() -> None:
    result = runner.invoke(
        app,
        ["workload", "evaluate", str(SMOKE), "--responses", str(PASSING)],
    )

    assert result.exit_code == 0
    assert "Evaluation passed" in result.stdout
    assert "100.0%" in result.stdout


def test_evaluation_quality_failure_exit_code() -> None:
    result = runner.invoke(
        app,
        ["workload", "evaluate", str(SMOKE), "--responses", str(MIXED)],
    )

    assert result.exit_code == 2
    assert "Evaluation completed with failures" in result.stdout
    assert "smoke-planning-001" in result.stdout


def test_evaluation_json_and_output_file(tmp_path: Path) -> None:
    output = tmp_path / "evaluation.json"
    result = runner.invoke(
        app,
        [
            "workload",
            "evaluate",
            str(SMOKE),
            "--responses",
            str(PASSING),
            "--json",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    stdout_payload = json.loads(result.stdout)
    file_payload = json.loads(output.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert stdout_payload["task_success_rate"] == 1.0


def test_invalid_response_fixture_exit_code(tmp_path: Path) -> None:
    invalid = tmp_path / "responses.jsonl"
    invalid.write_text('{"task_id":"x"}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        ["workload", "evaluate", str(SMOKE), "--responses", str(invalid)],
    )

    assert result.exit_code == 1
    assert "Schema validation failed" in result.stderr
