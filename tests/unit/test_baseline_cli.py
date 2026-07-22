from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aarchtune.cli import app

runner = CliRunner()


def _arguments(fake_binary: Path, fake_model: Path, output: Path) -> list[str]:
    workload = Path(__file__).resolve().parents[2] / "workloads/smoke-test.jsonl"
    return [
        "baseline",
        "--binary",
        str(fake_binary),
        "--model",
        str(fake_model),
        "--workload",
        str(workload),
        "--output-dir",
        str(output),
        "--startup-timeout",
        "2",
        "--request-timeout",
        "0.2",
        "--sample-interval",
        "0.05",
    ]


def test_baseline_human_success_is_explicitly_synthetic(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    result = runner.invoke(
        app,
        _arguments(fake_binary, fake_model, tmp_path / "human"),
        env={"FAKE_LLAMA_SCENARIO": "healthy-with-timings"},
    )
    assert result.exit_code == 0, result.output
    assert "AArchTune Baseline Complete" in result.output
    assert "synthetic fixture" in result.output
    assert "Arm64 verified:      no" in result.output
    assert "TTFT:                unavailable" in result.output


def test_baseline_json_output(tmp_path: Path, fake_binary: Path, fake_model: Path) -> None:
    result = runner.invoke(
        app,
        [*_arguments(fake_binary, fake_model, tmp_path / "json"), "--json"],
        env={"FAKE_LLAMA_SCENARIO": "healthy-without-timings"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert payload["summary"]["synthetic_fixture"] is True


def test_invalid_model_and_workload_exit_one(tmp_path: Path, fake_binary: Path) -> None:
    result = runner.invoke(
        app,
        [
            "baseline",
            "--binary",
            str(fake_binary),
            "--model",
            str(tmp_path / "missing.gguf"),
            "--workload",
            str(tmp_path / "missing.jsonl"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_existing_output_protection(tmp_path: Path, fake_binary: Path, fake_model: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    result = runner.invoke(app, _arguments(fake_binary, fake_model, output))
    assert result.exit_code == 1
    assert "not empty" in result.output
    assert (output / "keep.txt").read_text() == "keep"


def test_unsupported_explicit_flag_exits_one(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    binary = tmp_path / "fake-without-threads"
    source = fake_binary.read_text(encoding="utf-8")
    binary.write_text(source.replace("  --threads <count>\n", ""), encoding="utf-8")
    binary.chmod(0o755)
    result = runner.invoke(
        app,
        [*_arguments(binary, fake_model, tmp_path / "unsupported"), "--threads", "4"],
    )
    assert result.exit_code == 1
    assert "unsupported flag" in result.output


def test_quality_failures_exit_zero(tmp_path: Path, fake_binary: Path, fake_model: Path) -> None:
    result = runner.invoke(
        app,
        _arguments(fake_binary, fake_model, tmp_path / "quality"),
        env={"FAKE_LLAMA_SCENARIO": "mixed-task-quality"},
    )
    assert result.exit_code == 0
    assert "Task success:        80.0%" in result.output


def test_startup_failure_exits_two_and_partial_exits_three(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    failed = runner.invoke(
        app,
        _arguments(fake_binary, fake_model, tmp_path / "failed"),
        env={"FAKE_LLAMA_SCENARIO": "startup-failure"},
    )
    assert failed.exit_code == 2
    partial = runner.invoke(
        app,
        [*_arguments(fake_binary, fake_model, tmp_path / "partial"), "--warmup-requests", "0"],
        env={
            "FAKE_LLAMA_SCENARIO": "server-exits-mid-run",
            "FAKE_LLAMA_EXIT_AFTER": "2",
        },
    )
    assert partial.exit_code == 3
    assert "Partial" in partial.output
    assert (tmp_path / "partial/failure.json").is_file()
