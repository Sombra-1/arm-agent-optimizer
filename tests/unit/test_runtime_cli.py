from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from aarchtune.cli import app

runner = CliRunner()


def test_runtime_inspect_human(fake_binary: Path) -> None:
    result = runner.invoke(app, ["runtime", "inspect", "--binary", str(fake_binary)])

    assert result.exit_code == 0
    assert "AArchTune Runtime Inspection" in result.stdout
    assert "fake-llama-server 1.0" in result.stdout
    assert "--model" in result.stdout


def test_runtime_inspect_json(fake_binary: Path) -> None:
    result = runner.invoke(
        app,
        ["runtime", "inspect", "--binary", str(fake_binary), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "fake-llama-server 1.0 (synthetic)"
    assert "--port" in payload["supported_flags"]
    assert payload["version_probe"]["stdout"] is None


def test_runtime_inspect_missing_binary(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["runtime", "inspect", "--binary", str(tmp_path / "missing")],
    )

    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_smoke_start_success(fake_binary: Path, fake_model: Path) -> None:
    result = runner.invoke(
        app,
        [
            "runtime",
            "smoke-start",
            "--binary",
            str(fake_binary),
            "--model",
            str(fake_model),
        ],
    )

    assert result.exit_code == 0
    assert "smoke start complete" in result.stdout
    assert "Stopped:    yes" in result.stdout


def test_smoke_start_readiness_failure(
    fake_binary: Path, fake_model: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", "no-readiness")
    result = runner.invoke(
        app,
        [
            "runtime",
            "smoke-start",
            "--binary",
            str(fake_binary),
            "--model",
            str(fake_model),
            "--startup-timeout",
            "0.25",
        ],
    )

    assert result.exit_code == 1
    assert "not ready" in result.stderr


def test_smoke_start_artifact_generation(
    fake_binary: Path, fake_model: Path, tmp_path: Path
) -> None:
    output = tmp_path / "runtime-artifacts"
    result = runner.invoke(
        app,
        [
            "runtime",
            "smoke-start",
            "--binary",
            str(fake_binary),
            "--model",
            str(fake_model),
            "--output-dir",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["shutdown"]["stopped"] is True
    assert {path.name for path in output.iterdir()} == {
        "runtime-inspection.json",
        "server-command.json",
        "server-startup.log",
        "readiness.json",
        "shutdown.json",
    }
    command = json.loads((output / "server-command.json").read_text(encoding="utf-8"))
    assert isinstance(command["arguments"], list)
