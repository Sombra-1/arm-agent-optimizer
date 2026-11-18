from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aarchtune.baseline.models import BaselineRunConfig
from aarchtune.baseline.runner import run_baseline
from aarchtune.cli import app
from aarchtune.evaluation.quality_policy import load_quality_policy
from aarchtune.preflight.diagnostics import diagnose_baseline
from aarchtune.preflight.report import write_report

ROOT = Path(__file__).resolve().parents[2]
cli = CliRunner()


def _baseline(output: Path, scenario: str = "healthy", repetitions: int = 2) -> Path:
    result = run_baseline(
        BaselineRunConfig(
            binary_path=ROOT / "tests/fixtures/bin/fake-llama-server",
            model_path=ROOT / "tests/fixtures/models/fake-model.gguf",
            workload_path=ROOT / "workloads/smoke-test.jsonl",
            output_dir=output,
            repetitions=repetitions,
            warmup_requests=0,
            request_timeout_seconds=0.5,
            startup_timeout_seconds=2.0,
            sample_interval_seconds=0.05,
            maximum_consecutive_infrastructure_failures=100,
            extra_environment={"FAKE_LLAMA_SCENARIO": scenario},
        )
    )
    assert result.exit_code == 0
    return output


@pytest.fixture(scope="module")
def passing_baseline(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _baseline(tmp_path_factory.mktemp("preflight") / "baseline")


def test_passing_preflight_retains_configuration_until_optimization(passing_baseline: Path) -> None:
    report = diagnose_baseline(passing_baseline, load_quality_policy(None))
    assert report.can_tune and report.outcome == "quality_passed"
    assert report.synthetic and not report.is_arm64
    assert report.evidence_errors == []
    assert all(check.passed for check in report.checks)
    assert all(task.passed == task.attempts == 2 for task in report.tasks)
    assert "no verified improvement" in report.deployment_recommendation


@pytest.mark.parametrize(
    ("scenario", "category"),
    [("malformed-completion", "malformed_json"), ("http-500", "request_failed")],
)
def test_preflight_distinguishes_transport_and_formatting(
    tmp_path: Path, scenario: str, category: str
) -> None:
    baseline = _baseline(tmp_path / "baseline", scenario)
    report = diagnose_baseline(baseline, load_quality_policy(None))
    assert not report.can_tune and report.outcome == "quality_failed"
    assert any(category in task.failures for task in report.tasks)
    assert all("incorrect_value" not in task.failures for task in report.tasks)


def test_insufficient_repetitions_are_not_reported_as_model_failure(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path / "baseline", repetitions=1)
    report = diagnose_baseline(baseline, load_quality_policy(None))
    assert report.outcome == "evidence_incomplete" and not report.can_tune
    assert any(check.name == "repetitions_per_task" and not check.passed for check in report.checks)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "quality", "cleanup", "format"])
def test_preflight_rejects_inconsistent_evidence(
    passing_baseline: Path, tmp_path: Path, mutation: str
) -> None:
    copied = tmp_path / "baseline"
    shutil.copytree(passing_baseline, copied)
    raw = copied / "raw-attempts.jsonl"
    if mutation == "duplicate":
        lines = raw.read_text().splitlines()
        lines[1] = lines[0]
        raw.write_text("\n".join(lines) + "\n")
    elif mutation == "missing":
        (copied / "request-metrics.jsonl").unlink()
    elif mutation == "quality":
        path = copied / "baseline-summary.json"
        data = json.loads(path.read_text())
        data["quality"]["validator_pass_rate"] = 0.5
        path.write_text(json.dumps(data))
    elif mutation == "cleanup":
        path = copied / "manifest.json"
        data = json.loads(path.read_text())
        data["server_stopped"] = False
        path.write_text(json.dumps(data))
    else:
        path = copied / "request-metrics.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        lines[0]["request"]["response_format_mode"] = "json_object"
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    report = diagnose_baseline(copied, load_quality_policy(None))
    assert not report.can_tune and report.outcome == "evidence_incomplete"
    assert report.evidence_errors


def test_report_has_no_generated_responses_and_escapes_task_names(
    passing_baseline: Path, tmp_path: Path
) -> None:
    report = diagnose_baseline(passing_baseline, load_quality_policy(None))
    report.tasks[0].task_id = "<script>alert(1)</script>"
    write_report(report, tmp_path)
    html = (tmp_path / "report.html").read_text()
    machine = (tmp_path / "preflight.json").read_text()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "response_text" not in machine and "root_cause" not in machine
    assert "Synthetic test evidence" in html


def test_invalid_artifact_errors_do_not_echo_model_responses(
    passing_baseline: Path, tmp_path: Path
) -> None:
    baseline = tmp_path / "invalid"
    shutil.copytree(passing_baseline, baseline)
    secret_response = "PRIVATE_GENERATED_RESPONSE_123"
    (baseline / "raw-attempts.jsonl").write_text(
        json.dumps({"response_text": secret_response}) + "\n"
    )
    report = diagnose_baseline(baseline, load_quality_policy(None))
    assert report.outcome == "evidence_incomplete"
    assert secret_response not in report.model_dump_json()


def test_cli_can_diagnose_without_starting_server(passing_baseline: Path, tmp_path: Path) -> None:
    result = cli.invoke(
        app,
        [
            "preflight",
            "--baseline-dir",
            str(passing_baseline),
            "--output-dir",
            str(tmp_path / "diagnosis"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["can_tune"] is True
    assert (tmp_path / "diagnosis/report.html").is_file()


def test_cli_rejects_overlapping_output_before_overwrite(passing_baseline: Path) -> None:
    before = (passing_baseline / "manifest.json").read_bytes()
    result = cli.invoke(
        app,
        [
            "preflight",
            "--baseline-dir",
            str(passing_baseline),
            "--output-dir",
            str(passing_baseline.parent),
            "--overwrite",
        ],
    )
    assert result.exit_code == 1 and "overlap" in result.output
    assert (passing_baseline / "manifest.json").read_bytes() == before


def test_cli_missing_evidence_produces_diagnostic_artifacts(tmp_path: Path) -> None:
    result = cli.invoke(
        app,
        [
            "preflight",
            "--baseline-dir",
            str(tmp_path / "absent"),
            "--output-dir",
            str(tmp_path / "diagnosis"),
            "--json",
        ],
    )
    assert result.exit_code == 3
    assert json.loads(result.output)["outcome"] == "evidence_incomplete"
    assert (tmp_path / "diagnosis/report.html").is_file()


def test_cli_new_baseline_defaults_to_selected_policy_repetitions(tmp_path: Path) -> None:
    result = cli.invoke(
        app,
        [
            "preflight",
            "--binary",
            str(ROOT / "tests/fixtures/bin/fake-llama-server"),
            "--model",
            str(ROOT / "tests/fixtures/models/fake-model.gguf"),
            "--workload",
            str(ROOT / "workloads/smoke-test.jsonl"),
            "--quality-policy",
            str(ROOT / "configs/strict-quality-policy.yaml"),
            "--output-dir",
            str(tmp_path / "run"),
            "--json",
        ],
        env={"FAKE_LLAMA_SCENARIO": "healthy"},
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["can_tune"] is True
    summary = json.loads((tmp_path / "run/baseline/baseline-summary.json").read_text())
    assert summary["repetitions"] == 3


def test_cli_reports_wrong_values_and_quality_failure_exit_code(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path / "baseline", "mixed-task-quality")
    result = cli.invoke(
        app,
        [
            "preflight",
            "--baseline-dir",
            str(baseline),
            "--output-dir",
            str(tmp_path / "diagnosis"),
            "--json",
        ],
    )
    assert result.exit_code == 4
    report = json.loads(result.output)
    assert any("incorrect_value" in task["failures"] for task in report["tasks"])
    assert any("structured output alone" in item for item in report["recommendations"])
