"""Task evaluation and aggregate quality metrics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from aarchtune.workload.errors import EvaluationInputError
from aarchtune.workload.schema import (
    CategoryStatistics,
    LoadedWorkload,
    ResponseInput,
    TaskEvaluationResult,
    ValidatorResult,
    ValidatorType,
    ValidatorTypeStatistics,
    WorkloadEvaluationSummary,
)
from aarchtune.workload.validators import evaluate_validator, parse_response_json


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate_workload(
    workload: LoadedWorkload, responses: Iterable[ResponseInput]
) -> WorkloadEvaluationSummary:
    """Match responses by ID, evaluate every declaration, and preserve workload order."""

    task_ids = {task.id for task in workload.tasks}
    response_map: dict[str, ResponseInput] = {}
    for response_input in responses:
        if response_input.task_id in response_map:
            raise EvaluationInputError(f"Duplicate response task ID {response_input.task_id!r}")
        if response_input.task_id not in task_ids:
            raise EvaluationInputError(
                f"Response references unknown task ID {response_input.task_id!r}"
            )
        response_map[response_input.task_id] = response_input

    task_results: list[TaskEvaluationResult] = []
    parsed_validity: dict[str, bool] = {}
    for task in workload.tasks:
        response = response_map.get(task.id)
        if response is None:
            task_results.append(
                TaskEvaluationResult(
                    task_id=task.id,
                    category=task.category,
                    evaluated=False,
                    passed=None,
                    reason="No response was supplied for this task",
                )
            )
            continue
        parsed = parse_response_json(response.text)
        parsed_validity[task.id] = parsed.valid
        validator_results = [
            evaluate_validator(definition, response, parsed) for definition in task.validators
        ]
        passed = all(result.passed for result in validator_results)
        task_results.append(
            TaskEvaluationResult(
                task_id=task.id,
                category=task.category,
                evaluated=True,
                passed=passed,
                reason=None if passed else "One or more validators failed",
                validator_results=validator_results,
            )
        )

    return _summarize(workload, response_map, parsed_validity, task_results)


def _summarize(
    workload: LoadedWorkload,
    response_map: dict[str, ResponseInput],
    parsed_validity: dict[str, bool],
    task_results: list[TaskEvaluationResult],
) -> WorkloadEvaluationSummary:
    evaluated_results = [result for result in task_results if result.evaluated]
    validator_results = [
        validator
        for task_result in evaluated_results
        for validator in task_result.validator_results
    ]
    task_pass_count = sum(result.passed is True for result in evaluated_results)
    task_failure_count = sum(result.passed is False for result in evaluated_results)
    validator_pass_count = sum(result.passed for result in validator_results)
    total_validators = len(validator_results)
    evaluated_count = len(evaluated_results)

    per_category: dict[str, CategoryStatistics] = {}
    categories: dict[str, list[TaskEvaluationResult]] = defaultdict(list)
    for result in task_results:
        categories[result.category].append(result)
    for category, results in categories.items():
        evaluated = [result for result in results if result.evaluated]
        passed = sum(result.passed is True for result in evaluated)
        failed = sum(result.passed is False for result in evaluated)
        per_category[category] = CategoryStatistics(
            total_tasks=len(results),
            tasks_evaluated=len(evaluated),
            tasks_missing_responses=len(results) - len(evaluated),
            task_pass_count=passed,
            task_failure_count=failed,
            task_success_rate=_rate(passed, len(evaluated)),
        )

    grouped_validators: dict[ValidatorType, list[ValidatorResult]] = defaultdict(list)
    for validator_result in validator_results:
        grouped_validators[validator_result.validator].append(validator_result)
    per_validator_type = {
        validator_type: ValidatorTypeStatistics(
            total=len(results),
            passed=sum(result.passed for result in results),
            failed=sum(not result.passed for result in results),
            pass_rate=_rate(sum(result.passed for result in results), len(results)),
        )
        for validator_type, results in grouped_validators.items()
    }

    request_success_count = sum(response.request_succeeded for response in response_map.values())
    timeout_count = sum(response.timed_out for response in response_map.values())
    json_valid_count = sum(parsed_validity.values())
    unavailable_reasons: dict[str, str] = {}
    if evaluated_count == 0:
        unavailable_reasons.update(
            {
                "task_success_rate": "No tasks were evaluated",
                "json_validity_rate": "No tasks were evaluated",
                "request_success_rate": "No tasks were evaluated",
                "timeout_rate": "No tasks were evaluated",
            }
        )
    if total_validators == 0:
        unavailable_reasons["validator_pass_rate"] = "No validators were evaluated"

    return WorkloadEvaluationSummary(
        total_tasks=len(workload.tasks),
        tasks_evaluated=evaluated_count,
        tasks_missing_responses=len(workload.tasks) - evaluated_count,
        task_pass_count=task_pass_count,
        task_failure_count=task_failure_count,
        task_success_rate=_rate(task_pass_count, evaluated_count),
        total_validators=total_validators,
        validator_pass_count=validator_pass_count,
        validator_failure_count=total_validators - validator_pass_count,
        validator_pass_rate=_rate(validator_pass_count, total_validators),
        json_valid_response_count=json_valid_count,
        json_validity_rate=_rate(json_valid_count, evaluated_count),
        request_success_count=request_success_count,
        request_success_rate=_rate(request_success_count, evaluated_count),
        timeout_count=timeout_count,
        timeout_rate=_rate(timeout_count, evaluated_count),
        per_category=per_category,
        per_validator_type=per_validator_type,
        unavailable_reasons=unavailable_reasons,
        task_results=task_results,
    )
