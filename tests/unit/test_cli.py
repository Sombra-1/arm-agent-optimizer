from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from aarchtune.cli import app
from aarchtune.models import (
    BinaryInspection,
    CPUFeatures,
    HardwareReport,
    KleidiAIStatus,
    LlamaCppInspection,
)

runner = CliRunner()


def _report(*, arm64: bool) -> HardwareReport:
    server = BinaryInspection(name="llama-server", found=False, error="Not found on PATH")
    bench = BinaryInspection(name="llama-bench", found=False, error="Not found on PATH")
    return HardwareReport(
        architecture="aarch64" if arm64 else "x86_64",
        is_arm64=arm64,
        operating_system="Linux",
        kernel="6.0-test",
        cpu_model="Fixture CPU",
        logical_cores=8,
        physical_cores=4,
        memory_bytes=8 * 1024**3,
        memory_available_bytes=4 * 1024**3,
        numa_nodes=1,
        features=CPUFeatures(asimd=arm64),
        llama_cpp=LlamaCppInspection(
            server=server,
            bench=bench,
            kleidiai_status=KleidiAIStatus.UNKNOWN,
        ),
    )


def test_help_lists_doctor_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_doctor_json_is_machine_readable(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aarchtune.cli.detect_hardware", lambda model_path=None: _report(arm64=True)
    )

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["architecture"] == "aarch64"
    assert payload["is_arm64"] is True
    assert payload["llama_cpp"]["kleidiai_status"] == "unknown"


def test_doctor_writes_json_output(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aarchtune.cli.detect_hardware", lambda model_path=None: _report(arm64=True)
    )
    output = tmp_path / "nested" / "hardware-report.json"

    result = runner.invoke(app, ["doctor", "--output", str(output)])

    assert result.exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["cpu_model"] == "Fixture CPU"
    assert "JSON report written" in result.stdout


def test_non_arm_warning_is_explicit(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aarchtune.cli.detect_hardware", lambda model_path=None: _report(arm64=False)
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "This machine is not AArch64" in result.stdout
    assert "real Arm optimization results cannot be produced here" in result.stdout
