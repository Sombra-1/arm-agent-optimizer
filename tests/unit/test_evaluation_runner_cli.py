from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aarchtune.cli import app
from aarchtune.evaluation.models import (
    CandidateExecutionResult,
    CandidateExecutionStatus,
    EvaluationConfig,
    EvaluationStatus,
    SelectionOutcome,
)
from aarchtune.evaluation.runner import run_evaluation
from aarchtune.evaluation.validation import validate_evaluation_directory
from aarchtune.optimization.models import CandidateProfile

cli = CliRunner()


def _config(tmp_path: Path, screening: Path, name: str) -> EvaluationConfig:
    return EvaluationConfig(
        screening_dir=screening,
        output_dir=tmp_path / name,
        repetitions=2,
        warmup_requests=1,
        request_timeout_seconds=0.2,
        startup_timeout_seconds=2.0,
        shutdown_timeout_seconds=0.5,
        sample_interval_seconds=0.05,
        maximum_total_duration_seconds=120.0,
        allow_synthetic=True,
    )


@pytest.mark.parametrize(
    ("scenario", "outcome", "exit_code", "selected"),
    [
        ("profile-matrix", SelectionOutcome.CANDIDATE_SELECTED, 0, True),
        ("fast-quality-regression", SelectionOutcome.CANDIDATE_SELECTED, 0, True),
        ("baseline-remains-best", SelectionOutcome.BASELINE_RETAINED, 0, True),
        ("all-candidates-quality-fail", SelectionOutcome.NO_ELIGIBLE_CANDIDATE, 4, False),
        ("baseline-drift-quality", SelectionOutcome.INVALIDATED_BY_DRIFT, 3, False),
    ],
)
def test_evaluation_selection_scenarios_and_artifacts(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    outcome: SelectionOutcome,
    exit_code: int,
    selected: bool,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", scenario)
    result = run_evaluation(_config(tmp_path, evaluation_screening_dir, scenario))
    assert result.exit_code == exit_code
    assert result.selection is not None
    assert result.selection.outcome is outcome
    assert (result.output_dir / "selected-profile.yaml").exists() is selected
    validation = validate_evaluation_directory(result.output_dir)
    assert validation.valid, validation.errors
    assert not {
        "run-optimized.sh",
        "docker-compose.optimized.yaml",
        "optimization-passport.json",
        "report.html",
    }.intersection(item.name for item in result.output_dir.iterdir())
    manifest = json.loads((result.output_dir / "evaluation-manifest.json").read_text())
    assert manifest["owned_processes_stopped"] is True
    assert manifest["samplers_stopped"] is True


def test_candidate_isolation_warmup_order_and_no_process_reuse(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", "profile-matrix")
    result = run_evaluation(_config(tmp_path, evaluation_screening_dir, "isolation"))
    plan = json.loads((result.output_dir / "execution-plan.json").read_text())
    candidates = [
        json.loads(line)
        for line in (result.output_dir / "candidate-results.jsonl").read_text().splitlines()
    ]
    assert [item["candidate_id"] for item in candidates] == plan["execution_order"][1:-1]
    assert len({item["run_id"] for item in candidates}) == len(candidates)
    for candidate in candidates:
        directory = Path(candidate["run_directory"])
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest["completed_attempt_count"] == len(plan["task_order"]) * 2
        summary = json.loads((directory / "baseline-summary.json").read_text())
        assert summary["execution"]["warmup_request_count"] == 1
        attempts = [
            json.loads(line) for line in (directory / "raw-attempts.jsonl").read_text().splitlines()
        ]
        assert [item["task_id"] for item in attempts[: len(plan["task_order"])]] == plan[
            "task_order"
        ]


def test_candidate_failures_are_isolated_and_cleanup_preserved(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", "candidate-startup-failure")
    result = run_evaluation(_config(tmp_path, evaluation_screening_dir, "startup-fail"))
    assert result.status is EvaluationStatus.FAILED
    manifest = json.loads((result.output_dir / "evaluation-manifest.json").read_text())
    assert manifest["owned_processes_stopped"] is True
    assert manifest["samplers_stopped"] is True


def test_cli_human_json_validation_and_synthetic_language(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", "profile-matrix")
    output = tmp_path / "cli"
    result = cli.invoke(
        app,
        [
            "evaluate",
            "--screening",
            str(evaluation_screening_dir),
            "--output-dir",
            str(output),
            "--repetitions",
            "2",
            "--request-timeout",
            "0.2",
            "--startup-timeout",
            "2",
            "--sample-interval",
            "0.05",
            "--allow-synthetic",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Real-Workload Evaluation Complete" in result.output
    assert "Synthetic real-workload measurements" in result.output
    assert "state of the art" not in result.output.lower()
    validated = cli.invoke(app, ["evaluate", "validate", str(output), "--json"])
    assert validated.exit_code == 0
    assert json.loads(validated.output)["valid"] is True


def test_validation_detects_selection_forbidden_and_candidate_tampering(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", "profile-matrix")
    result = run_evaluation(_config(tmp_path, evaluation_screening_dir, "tamper"))
    (result.output_dir / "report.html").write_text("forbidden")
    selection_path = result.output_dir / "selection.json"
    selection = json.loads(selection_path.read_text())
    selection["selected_candidate_hash"] = "0" * 64
    selection_path.write_text(json.dumps(selection))
    validation = validate_evaluation_directory(result.output_dir)
    assert not validation.valid
    assert any("Forbidden" in error for error in validation.errors)
    assert any("selected profile" in error.lower() for error in validation.errors)


def test_simulated_interruption_marks_manifest_and_preserves_cleanup(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aarchtune.evaluation import runner

    def interrupted_profile(**kwargs: object) -> CandidateExecutionResult:
        profile = CandidateProfile.model_validate(kwargs["profile"])
        directory = Path(str(kwargs["directory"]))
        directory.mkdir(parents=True)
        return CandidateExecutionResult(
            candidate_id=profile.id,
            candidate_hash=profile.profile_hash,
            label=str(kwargs["label"]),
            profile=profile,
            status=CandidateExecutionStatus.INTERRUPTED,
            run_id="synthetic-interruption",
            run_directory=directory,
            screening_score=None,
            performance=None,
            quality=None,
            failure_type="KeyboardInterrupt",
            failure_message="Synthetic user interruption",
            server_stopped=True,
            sampler_stopped=True,
        )

    monkeypatch.setattr(runner, "_run_profile", interrupted_profile)
    result = run_evaluation(_config(tmp_path, evaluation_screening_dir, "interrupted"))
    assert result.exit_code == 3
    assert result.status is EvaluationStatus.INTERRUPTED
    manifest = json.loads((result.output_dir / "evaluation-manifest.json").read_text())
    assert manifest["status"] == "interrupted"
    assert manifest["owned_processes_stopped"] is True
    assert manifest["samplers_stopped"] is True
    assert (result.output_dir / "execution-plan.json").is_file()
