from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from aarchtune.workload import loader
from aarchtune.workload.errors import WorkloadLoadError
from aarchtune.workload.loader import load_workload
from aarchtune.workload.schema import (
    MAX_MESSAGE_CONTENT_CHARACTERS,
    MAX_MESSAGES_PER_TASK,
    MAX_REGEX_CHARACTERS,
    MAX_VALIDATORS_PER_TASK,
)


def _task(task_id: str = "task-1") -> dict[str, object]:
    return {
        "id": task_id,
        "category": "test",
        "description": "A deterministic test task.",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "generation": {"temperature": 0, "max_tokens": 20, "seed": 42},
        "validators": [{"type": "valid_json"}],
    }


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> bytes:
    content = b"".join(
        json.dumps(record, separators=(",", ":")).encode() + b"\n" for record in records
    )
    path.write_bytes(content)
    return content


def test_load_valid_workload_preserves_order_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "workload.jsonl"
    content = _write_jsonl(path, [_task("first"), _task("second")])

    workload = load_workload(path)

    assert [task.id for task in workload.tasks] == ["first", "second"]
    assert workload.sha256 == hashlib.sha256(content).hexdigest()
    assert workload.byte_size == len(content)


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "workload.jsonl"
    path.write_text(f"\n  \n{json.dumps(_task())}\n\n", encoding="utf-8")

    assert len(load_workload(path).tasks) == 1


@pytest.mark.parametrize("content", [b"", b"\n \n"])
def test_empty_workload_is_rejected(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_bytes(content)

    with pytest.raises(WorkloadLoadError, match="contains no tasks"):
        load_workload(path)


def test_invalid_utf8_reports_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_bytes(json.dumps(_task()).encode() + b"\n\xff\n")

    with pytest.raises(WorkloadLoadError, match=r"UTF-8.*line 2"):
        load_workload(path)


def test_invalid_json_reports_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")

    with pytest.raises(WorkloadLoadError, match=r"Invalid JSON.*line 1"):
        load_workload(path)


def test_comments_are_explicitly_rejected(tmp_path: Path) -> None:
    path = tmp_path / "commented.jsonl"
    path.write_text(f"# synthetic\n{json.dumps(_task())}\n", encoding="utf-8")

    with pytest.raises(WorkloadLoadError, match="Comments are not supported"):
        load_workload(path)


def test_duplicate_ids_report_line_and_id(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.jsonl"
    _write_jsonl(path, [_task("duplicate"), _task("duplicate")])

    with pytest.raises(WorkloadLoadError, match=r"Duplicate task ID.*line 2.*duplicate"):
        load_workload(path)


def test_oversized_file_is_rejected_before_read(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "large.jsonl"
    path.write_bytes(b"123456")
    monkeypatch.setattr(loader, "MAX_WORKLOAD_FILE_BYTES", 5)

    with pytest.raises(WorkloadLoadError, match="maximum is 5 bytes"):
        load_workload(path)


def test_oversized_line_is_rejected(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "line.jsonl"
    _write_jsonl(path, [_task()])
    monkeypatch.setattr(loader, "MAX_JSONL_LINE_BYTES", 10)

    with pytest.raises(WorkloadLoadError, match="JSONL line"):
        load_workload(path)


def test_too_many_tasks_is_rejected(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "many.jsonl"
    _write_jsonl(path, [_task("one"), _task("two")])
    monkeypatch.setattr(loader, "MAX_TASKS", 1)

    with pytest.raises(WorkloadLoadError, match="maximum of 1 tasks"):
        load_workload(path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"unexpected": True}, "Extra inputs are not permitted"),
        ({"validators": [{"type": "made_up"}]}, "does not match any"),
        ({"validators": [{"type": "exact_value", "path": "$.x"}]}, "expected"),
        (
            {"validators": [{"type": "json_schema", "schema": {"type": "made_up"}}]},
            "invalid Draft 2020-12",
        ),
        ({"validators": [{"type": "regex_match", "pattern": "("}]}, "regular expression"),
    ],
)
def test_schema_failures_are_clear(tmp_path: Path, change: dict[str, object], message: str) -> None:
    record = _task("identified-task")
    record.update(change)
    path = tmp_path / "invalid.jsonl"
    _write_jsonl(path, [record])

    with pytest.raises(WorkloadLoadError) as raised:
        load_workload(path)

    assert message in str(raised.value)
    assert "identified-task" in str(raised.value)
    assert "line 1" in str(raised.value)


def test_hash_uses_exact_input_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    serialized = json.dumps(_task())
    first.write_text(f"{serialized}\n", encoding="utf-8")
    second.write_text(f"{serialized}\n\n", encoding="utf-8")

    assert load_workload(first).sha256 != load_workload(second).sha256


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "messages",
            [{"role": "user", "content": "x"}] * (MAX_MESSAGES_PER_TASK + 1),
            "at most 32 items",
        ),
        (
            "messages",
            [{"role": "user", "content": "x" * (MAX_MESSAGE_CONTENT_CHARACTERS + 1)}],
            "at most 65536 characters",
        ),
        (
            "validators",
            [{"type": "valid_json"}] * (MAX_VALIDATORS_PER_TASK + 1),
            "at most 32 items",
        ),
        (
            "validators",
            [{"type": "regex_match", "pattern": "x" * (MAX_REGEX_CHARACTERS + 1)}],
            "at most 1024 characters",
        ),
    ],
)
def test_per_task_safety_limits(tmp_path: Path, field: str, value: object, message: str) -> None:
    record = _task()
    record[field] = value
    path = tmp_path / "limited.jsonl"
    _write_jsonl(path, [record])

    with pytest.raises(WorkloadLoadError, match=message):
        load_workload(path)


@pytest.mark.parametrize(
    "generation",
    [
        {"temperature": -0.1, "max_tokens": 20, "seed": 42},
        {"temperature": 2.1, "max_tokens": 20, "seed": 42},
        {"temperature": 0, "max_tokens": 0, "seed": 42},
        {"temperature": 0, "max_tokens": 32_769, "seed": 42},
    ],
)
def test_generation_limits(tmp_path: Path, generation: dict[str, object]) -> None:
    record = _task()
    record["generation"] = generation
    path = tmp_path / "generation.jsonl"
    _write_jsonl(path, [record])

    with pytest.raises(WorkloadLoadError, match="Schema validation failed"):
        load_workload(path)
