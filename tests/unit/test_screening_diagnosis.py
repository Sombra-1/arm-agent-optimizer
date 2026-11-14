from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aarchtune.cli import app
from aarchtune.screening.diagnostics import diagnose_screening
from aarchtune.screening.models import ScreeningConfig
from aarchtune.screening.runner import run_screening

cli = CliRunner()


def test_offline_diagnosis_preserves_sources_and_does_not_run_binaries(
    evaluation_screening_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline diagnosis attempted to start a process")

    before = {path: path.read_bytes() for path in evaluation_screening_dir.glob("*.json*")}
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    output = tmp_path / "offline-report"
    result = cli.invoke(
        app,
        [
            "screen",
            "diagnose",
            str(evaluation_screening_dir),
            "--output-dir",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["coverage"]["planned_profiles"] == len(data["candidates"])
    assert data["coverage"]["advanced_profiles"] == 4
    assert data["coverage"]["workload_completed_profiles"] is None
    assert data["coverage"]["usable_invocation_fraction"] == 1.0
    assert any(item["server_only_fields"] for item in data["candidates"])
    assert all(path.read_bytes() == content for path, content in before.items())
    page = (output / "screening-report.html").read_text()
    assert "SYNTHETIC TEST EVIDENCE" in page
    assert "Advancement is not completed workload evaluation" in page
    assert "http://" not in page and "https://" not in page


@pytest.mark.parametrize(
    ("name", "unknown_count"),
    [
        ("raw-executions.jsonl", "attempted_invocations"),
        ("normalized-measurements.jsonl", "usable_invocations"),
        ("advancement-decisions.jsonl", "advanced_profiles"),
        ("bench-signatures.jsonl", "low_level_signatures"),
    ],
)
def test_missing_metadata_is_unavailable_instead_of_zero(
    evaluation_screening_dir: Path, name: str, unknown_count: str
) -> None:
    (evaluation_screening_dir / name).unlink()
    result = cli.invoke(app, ["screen", "diagnose", str(evaluation_screening_dir), "--json"])
    assert result.exit_code == 3
    data = json.loads(result.output)
    assert data["coverage"][unknown_count] is None
    assert any(name in message for message in data["evidence_errors"])
    assert len(data["candidates"]) == data["coverage"]["planned_profiles"]


def test_invalid_metadata_does_not_echo_validation_input(evaluation_screening_dir: Path) -> None:
    (evaluation_screening_dir / "raw-executions.jsonl").write_text(
        '{"response_text":"PRIVATE_INPUT"}\n'
    )
    result = cli.invoke(app, ["screen", "diagnose", str(evaluation_screening_dir), "--json"])
    assert result.exit_code == 3
    data = json.loads(result.output)
    assert data["coverage"]["attempted_invocations"] is None
    assert "PRIVATE_INPUT" not in result.output
    assert "response_text" not in result.output


def test_no_metadata_cannot_establish_empty_search(tmp_path: Path) -> None:
    report = diagnose_screening(tmp_path / "missing")
    assert report.evidence_errors
    assert all(value is None for value in report.coverage.values())
    assert report.candidates == []
    assert report.source_sha256 == {}


@pytest.mark.parametrize("failure", ["budget", "process_start"])
def test_unattempted_budget_and_failed_process_start_are_distinct(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from aarchtune.screening import runner

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text(
        "schema_version: '1.0'\nscenarios:\n"
        "  - {id: decode, prompt_tokens: 0, generation_tokens: 16}\n"
    )
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")

    def cannot_start(*args: object, **kwargs: object) -> None:
        raise OSError("PRIVATE_PROCESS_ERROR")

    monkeypatch.setattr(runner, "run_one_shot", cannot_start)
    result = run_screening(
        ScreeningConfig(
            plan_dir=screen_plan_dir,
            bench_binary=fake_bench,
            output_dir=tmp_path / "failed-screening",
            scenario_path=scenario,
            repetitions=1,
            allow_synthetic=True,
        ),
        deadline_monotonic=0.0 if failure == "budget" else None,
    )
    report = diagnose_screening(result.output_dir)
    assert not report.evidence_errors
    planned = report.coverage["planned_invocations"]
    assert planned > 0
    assert report.coverage["attempted_invocations"] == (0 if failure == "budget" else planned)
    expected = "not_attempted_budget" if failure == "budget" else "process_start_failed"
    assert all(item["status"] == expected for item in report.invocations)
    assert "PRIVATE_PROCESS_ERROR" not in report.model_dump_json()
    assert report.next_steps


def test_report_escapes_reason_text(evaluation_screening_dir: Path, tmp_path: Path) -> None:
    from aarchtune.screening.report import write_screening_report

    report = diagnose_screening(evaluation_screening_dir)
    report.candidates[0]["reason"] = "<script>unsafe()</script>"
    write_screening_report(report, tmp_path / "escaped")
    page = (tmp_path / "escaped/screening-report.html").read_text()
    assert "<script>" not in page
    assert "&lt;script&gt;unsafe()&lt;/script&gt;" in page
