from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from aarchtune.workload import evaluation
from aarchtune.workload.errors import EvaluationInputError
from aarchtune.workload.loader import load_workload
from aarchtune.workload.schema import ResponseInput, ValidatorType


def _task(
    task_id: str,
    category: str = "category-a",
    validators: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": task_id,
        "category": category,
        "description": "Synthetic evaluation task.",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "generation": {"temperature": 0, "max_tokens": 20, "seed": 42},
        "validators": validators
        or [
            {"type": "request_succeeded"},
            {"type": "valid_json"},
            {"type": "exact_value", "path": "$.status", "expected": "ok"},
        ],
    }


def _workload(tmp_path: Path, tasks: list[dict[str, object]]) -> object:
    path = tmp_path / "workload.jsonl"
    path.write_text("".join(f"{json.dumps(task)}\n" for task in tasks), encoding="utf-8")
    return load_workload(path)


def _response(
    task_id: str,
    text: str = '{"status":"ok"}',
    *,
    request_succeeded: bool = True,
    timed_out: bool = False,
) -> ResponseInput:
    return ResponseInput(
        task_id=task_id,
        text=text,
        request_succeeded=request_succeeded,
        timed_out=timed_out,
        status_code=200 if request_succeeded else None,
        error=None if request_succeeded else "synthetic failure",
    )


def test_all_pass_task_and_aggregate_metrics(tmp_path: Path) -> None:
    workload = _workload(tmp_path, [_task("one")])

    summary = evaluation.evaluate_workload(workload, [_response("one")])

    assert summary.task_pass_count == 1
    assert summary.task_success_rate == 1.0
    assert summary.validator_pass_count == 3
    assert summary.validator_pass_rate == 1.0
    assert summary.json_validity_rate == 1.0
    assert summary.request_success_rate == 1.0
    assert summary.timeout_rate == 0.0


def test_multiple_validator_failures_are_preserved(tmp_path: Path) -> None:
    workload = _workload(tmp_path, [_task("one")])

    summary = evaluation.evaluate_workload(
        workload,
        [_response("one", "not json", request_succeeded=False, timed_out=True)],
    )

    result = summary.task_results[0]
    assert result.passed is False
    assert len(result.validator_results) == 3
    assert [item.passed for item in result.validator_results] == [False, False, False]
    assert summary.timeout_count == 1


def test_response_json_is_parsed_once_per_evaluated_task(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    workload = _workload(
        tmp_path,
        [
            _task(
                "one",
                validators=[
                    {"type": "valid_json"},
                    {"type": "required_fields", "paths": ["$.status"]},
                    {"type": "exact_value", "path": "$.status", "expected": "ok"},
                ],
            )
        ],
    )
    original = evaluation.parse_response_json
    calls = 0

    def counting_parser(text: str) -> object:
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(evaluation, "parse_response_json", counting_parser)

    summary = evaluation.evaluate_workload(workload, [_response("one")])

    assert summary.task_pass_count == 1
    assert calls == 1


def test_missing_response_is_not_counted_as_evaluated_failure(tmp_path: Path) -> None:
    workload = _workload(tmp_path, [_task("one"), _task("two")])

    summary = evaluation.evaluate_workload(workload, [_response("one")])

    assert summary.tasks_evaluated == 1
    assert summary.tasks_missing_responses == 1
    assert summary.task_failure_count == 0
    assert summary.task_results[1].evaluated is False
    assert summary.task_results[1].passed is None


def test_zero_denominators_are_null_with_reasons(tmp_path: Path) -> None:
    workload = _workload(tmp_path, [_task("one")])

    summary = evaluation.evaluate_workload(workload, [])

    assert summary.task_success_rate is None
    assert summary.validator_pass_rate is None
    assert summary.json_validity_rate is None
    assert summary.request_success_rate is None
    assert summary.timeout_rate is None
    assert summary.unavailable_reasons["task_success_rate"] == "No tasks were evaluated"


def test_category_and_validator_type_statistics(tmp_path: Path) -> None:
    workload = _workload(
        tmp_path,
        [_task("one", "alpha"), _task("two", "beta"), _task("three", "alpha")],
    )

    summary = evaluation.evaluate_workload(
        workload,
        [_response("one"), _response("two", '{"status":"bad"}')],
    )

    assert summary.per_category["alpha"].total_tasks == 2
    assert summary.per_category["alpha"].tasks_missing_responses == 1
    assert summary.per_category["alpha"].task_success_rate == 1.0
    assert summary.per_category["beta"].task_success_rate == 0.0
    exact_stats = summary.per_validator_type[ValidatorType.EXACT_VALUE]
    assert exact_stats.total == 2
    assert exact_stats.passed == 1
    assert exact_stats.failed == 1
    assert exact_stats.pass_rate == 0.5


def test_unknown_response_task_id_is_rejected(tmp_path: Path) -> None:
    workload = _workload(tmp_path, [_task("one")])

    with pytest.raises(EvaluationInputError, match="unknown task ID"):
        evaluation.evaluate_workload(workload, [_response("unknown")])


def test_duplicate_response_task_id_is_rejected(tmp_path: Path) -> None:
    workload = _workload(tmp_path, [_task("one")])

    with pytest.raises(EvaluationInputError, match="Duplicate response"):
        evaluation.evaluate_workload(workload, [_response("one"), _response("one")])
