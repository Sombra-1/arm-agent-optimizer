"""Bounded UTF-8 JSONL loading with exact-byte provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aarchtune.workload.errors import ResponseFixtureError, WorkloadLoadError
from aarchtune.workload.schema import (
    MAX_JSONL_LINE_BYTES,
    MAX_TASKS,
    MAX_WORKLOAD_FILE_BYTES,
    LoadedWorkload,
    ResponseInput,
    WorkloadTask,
    WorkloadValidationSummary,
)


def _read_bounded(path: Path, *, fixture: bool) -> bytes:
    error_type = ResponseFixtureError if fixture else WorkloadLoadError
    expanded = path.expanduser()
    try:
        size = expanded.stat().st_size
    except OSError as exc:
        raise error_type(f"Cannot stat {expanded}: {exc}") from exc
    if not expanded.is_file():
        raise error_type(f"Path is not a regular file: {expanded}")
    if size > MAX_WORKLOAD_FILE_BYTES:
        raise error_type(f"File is {size} bytes; maximum is {MAX_WORKLOAD_FILE_BYTES} bytes")
    try:
        content = expanded.read_bytes()
    except OSError as exc:
        raise error_type(f"Cannot read {expanded}: {exc}") from exc
    if len(content) > MAX_WORKLOAD_FILE_BYTES:
        raise error_type(
            f"File grew beyond the {MAX_WORKLOAD_FILE_BYTES}-byte limit while being read"
        )
    return content


def _jsonl_lines(content: bytes, *, fixture: bool) -> Iterator[tuple[int, str]]:
    error_type = ResponseFixtureError if fixture else WorkloadLoadError
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        if len(raw_line) > MAX_JSONL_LINE_BYTES:
            raise error_type(
                f"JSONL line is {len(raw_line)} bytes; maximum is {MAX_JSONL_LINE_BYTES}",
                line_number=line_number,
            )
        if not raw_line.strip():
            continue
        if raw_line.lstrip().startswith(b"#"):
            raise error_type(
                "Comments are not supported; every non-blank line must be JSON",
                line_number=line_number,
            )
        try:
            yield line_number, raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise error_type(
                f"Line is not valid UTF-8 at byte offset {exc.start}",
                line_number=line_number,
            ) from exc


def _task_id_hint(value: Any) -> str | None:
    if isinstance(value, dict):
        task_id = value.get("id")
        return task_id if isinstance(task_id, str) else None
    return None


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0]
    location = ".".join(str(item) for item in first["loc"])
    message = str(first["msg"])
    return f"Schema validation failed at {location}: {message}" if location else message


def load_workload(path: Path) -> LoadedWorkload:
    """Load a strict workload while preserving task order and exact-byte SHA-256."""

    content = _read_bounded(path, fixture=False)
    tasks: list[WorkloadTask] = []
    ids: set[str] = set()
    for line_number, line in _jsonl_lines(content, fixture=False):
        try:
            raw_task: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkloadLoadError(
                f"Invalid JSON at column {exc.colno}: {exc.msg}", line_number=line_number
            ) from exc
        task_id = _task_id_hint(raw_task)
        try:
            task = WorkloadTask.model_validate_json(line)
        except ValidationError as exc:
            raise WorkloadLoadError(
                _validation_message(exc), line_number=line_number, task_id=task_id
            ) from exc
        if task.id in ids:
            raise WorkloadLoadError("Duplicate task ID", line_number=line_number, task_id=task.id)
        ids.add(task.id)
        tasks.append(task)
        if len(tasks) > MAX_TASKS:
            raise WorkloadLoadError(
                f"Workload exceeds the maximum of {MAX_TASKS} tasks",
                line_number=line_number,
                task_id=task.id,
            )
    if not tasks:
        raise WorkloadLoadError("Workload contains no tasks")
    return LoadedWorkload(
        path=path,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        tasks=tasks,
    )


def load_response_fixtures(path: Path) -> list[ResponseInput]:
    """Load synthetic response metadata with the same bounded JSONL rules."""

    content = _read_bounded(path, fixture=True)
    responses: list[ResponseInput] = []
    ids: set[str] = set()
    for line_number, line in _jsonl_lines(content, fixture=True):
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResponseFixtureError(
                f"Invalid JSON at column {exc.colno}: {exc.msg}", line_number=line_number
            ) from exc
        try:
            response = ResponseInput.model_validate_json(line)
        except ValidationError as exc:
            raise ResponseFixtureError(_validation_message(exc), line_number=line_number) from exc
        if response.task_id in ids:
            raise ResponseFixtureError(
                f"Duplicate response task ID {response.task_id!r}", line_number=line_number
            )
        ids.add(response.task_id)
        responses.append(response)
        if len(responses) > MAX_TASKS:
            raise ResponseFixtureError(
                f"Response fixture exceeds the maximum of {MAX_TASKS} records",
                line_number=line_number,
            )
    return responses


def summarize_workload(workload: LoadedWorkload) -> WorkloadValidationSummary:
    """Create deterministic counts used by human and JSON CLI output."""

    category_counts: dict[str, int] = {}
    validator_counts: dict[Any, int] = {}
    validator_total = 0
    for task in workload.tasks:
        category_counts[task.category] = category_counts.get(task.category, 0) + 1
        for definition in task.validators:
            validator_counts[definition.type] = validator_counts.get(definition.type, 0) + 1
            validator_total += 1
    return WorkloadValidationSummary(
        path=workload.path,
        sha256=workload.sha256,
        byte_size=workload.byte_size,
        tasks=len(workload.tasks),
        categories=len(category_counts),
        category_counts=category_counts,
        validators=validator_total,
        validator_counts=validator_counts,
        deterministic=workload.deterministic,
    )
