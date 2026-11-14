from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aarchtune.cli import app
from aarchtune.workload.evaluation import evaluate_workload
from aarchtune.workload.loader import load_workload
from aarchtune.workload.schema import ResponseInput

ROOT = Path(__file__).resolve().parents[2]


def test_workload_init_creates_valid_template_and_refuses_overwrite(tmp_path: Path) -> None:
    cli = CliRunner()
    output = tmp_path / "my-workload.jsonl"
    result = cli.invoke(app, ["workload", "init", "--output", str(output)])
    assert result.exit_code == 0, result.output
    workload = load_workload(output)
    assert workload.deterministic and len(workload.tasks) == 5
    assert all(task.output_schema is not None for task in workload.tasks)
    original = output.read_bytes()
    repeated = cli.invoke(app, ["workload", "init", "--output", str(output)])
    assert repeated.exit_code == 1 and output.read_bytes() == original


def test_packaged_defaults_match_repository_sources() -> None:
    data = ROOT / "src/aarchtune/data"
    for name in (
        "default-quality-policy.yaml",
        "default-search-space.yaml",
        "default-screening-scenarios.yaml",
    ):
        assert (data / name).read_bytes() == (ROOT / "configs" / name).read_bytes()
    assert (data / "support-triage.jsonl").read_bytes() == (
        ROOT / "workloads/support-triage.jsonl"
    ).read_bytes()


def test_starter_contract_accepts_correct_answers_and_rejects_valid_json_with_wrong_decisions() -> (
    None
):
    workload = load_workload(ROOT / "workloads/support-triage.jsonl")
    correct: list[ResponseInput] = []
    for task, queue, priority in zip(
        workload.tasks,
        ["billing", "account", "technical", "technical", "unknown"],
        ["normal", "normal", "normal", "urgent", "normal"],
        strict=True,
    ):
        correct.append(
            ResponseInput(
                task_id=task.id,
                text=json.dumps(
                    {
                        "ticket_id": task.id.removeprefix("support-"),
                        "queue": queue,
                        "priority": priority,
                    }
                ),
                request_succeeded=True,
                timed_out=False,
            )
        )
    passed = evaluate_workload(workload, correct)
    assert passed.task_success_rate == 1.0
    wrong = [
        response.model_copy(
            update={
                "text": json.dumps({"ticket_id": "T-000", "queue": "unknown", "priority": "normal"})
            }
        )
        for response in correct
    ]
    failed = evaluate_workload(workload, wrong)
    assert failed.json_validity_rate == 1.0 and failed.task_success_rate == 0.0
