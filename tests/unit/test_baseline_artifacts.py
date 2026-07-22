from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aarchtune.baseline.artifacts import (
    PROJECT_ROOT,
    JsonlArtifactWriter,
    atomic_write_json,
    prepare_run_directory,
)
from aarchtune.baseline.errors import BaselineInputError
from aarchtune.baseline.manifest import ManifestManager
from aarchtune.baseline.models import RunStage, RunStatus


def test_new_output_directory_is_created(tmp_path: Path) -> None:
    output = prepare_run_directory(tmp_path / "new", overwrite=False)
    assert output.is_dir()


def test_existing_nonempty_directory_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BaselineInputError, match="not empty"):
        prepare_run_directory(output, overwrite=False)


def test_safe_overwrite_removes_old_content(tmp_path: Path) -> None:
    output = tmp_path / "run"
    (output / "nested").mkdir(parents=True)
    (output / "nested/old.txt").write_text("old", encoding="utf-8")
    assert prepare_run_directory(output, overwrite=True) == output.resolve()
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("dangerous", [Path("/"), Path.home(), PROJECT_ROOT])
def test_dangerous_output_directories_are_rejected(dangerous: Path) -> None:
    with pytest.raises(BaselineInputError, match="dangerous"):
        prepare_run_directory(dangerous, overwrite=True)


def test_atomic_json_replaces_old_document(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    atomic_write_json(path, {"state": "initializing"})
    atomic_write_json(path, {"state": "completed"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"state": "completed"}
    assert not list(tmp_path.glob(".manifest.json.*"))


def test_jsonl_writer_streams_records(tmp_path: Path) -> None:
    path = tmp_path / "attempts.jsonl"
    with JsonlArtifactWriter(path) as writer:
        writer.append({"attempt": 1})
        writer.append({"attempt": 2})
    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"attempt": 1},
        {"attempt": 2},
    ]


def test_manifest_progression_and_terminal_metadata(tmp_path: Path) -> None:
    created = datetime.now(UTC)
    manager = ManifestManager(tmp_path, "run-1", created)
    first = json.loads(manager.path.read_text())
    assert first["schema_version"] == "1.0"
    assert first["status"] == "initializing"
    manager.update(stage=RunStage.MEASURING, status=RunStatus.RUNNING)
    manager.update(
        stage=RunStage.COMPLETED,
        status=RunStatus.COMPLETED,
        completed_attempt_count=5,
        server_stopped=True,
        sampler_stopped=True,
    )
    final = json.loads(manager.path.read_text())
    assert final["stage"] == "completed"
    assert final["status"] == "completed"
    assert final["completed_attempt_count"] == 5
