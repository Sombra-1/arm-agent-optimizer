from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aarchtune.baseline.models import (
    BaselineManifest,
    FileProvenance,
    HashProvenance,
    RunStage,
    RunStatus,
)
from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.cli import app
from aarchtune.hardware.detector import detect_hardware
from aarchtune.optimization.artifacts import validate_plan_directory
from aarchtune.optimization.compatibility import load_baseline_input
from aarchtune.optimization.errors import (
    BaselineReferenceError,
    ProvenanceMismatchError,
)
from aarchtune.runtime.capabilities import inspect_llama_server_capabilities
from aarchtune.runtime.command import build_llama_server_command
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.workload.loader import load_workload, summarize_workload

runner = CliRunner()


def _workload() -> Path:
    return Path(__file__).resolve().parents[2] / "workloads/smoke-test.jsonl"


def _dump(path: Path, value: object) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _envelope(data: object) -> dict[str, object]:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    return {
        "schema_version": "1.0",
        "run_id": "synthetic-plan-baseline",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "completed",
        "data": data,
    }


def _make_baseline(root: Path, fake_binary: Path, fake_model: Path) -> Path:
    root.mkdir()
    capabilities = inspect_llama_server_capabilities(
        fake_binary, include_probe_output=True, use_cache=False
    )
    hardware = detect_hardware(model_path=fake_model)
    workload = load_workload(_workload())
    workload_summary = summarize_workload(workload)
    binary_stat = fake_binary.stat()
    model_stat = fake_model.stat()
    binary_info = FileProvenance(
        path=str(fake_binary),
        filename=fake_binary.name,
        size_bytes=binary_stat.st_size,
        modification_time_ns=binary_stat.st_mtime_ns,
        hash=HashProvenance(value=hash_file_streaming(fake_binary), completed=True),
        synthetic_fixture=True,
    )
    model_info = FileProvenance(
        path=str(fake_model),
        filename=fake_model.name,
        size_bytes=model_stat.st_size,
        modification_time_ns=model_stat.st_mtime_ns,
        hash=HashProvenance(value=hash_file_streaming(fake_model), completed=True),
        synthetic_fixture=True,
    )
    config = LlamaServerConfig(
        binary_path=fake_binary,
        model_path=fake_model,
        port=12345,
        threads=4,
        threads_batch=8,
        batch_size=512,
        ubatch_size=128,
        context_size=4096,
        parallel_slots=1,
        prompt_cache=False,
        mmap=True,
    )
    command = build_llama_server_command(config, capabilities)
    created = datetime.now(UTC)
    manifest = BaselineManifest(
        run_id="synthetic-plan-baseline",
        created_at=created,
        status=RunStatus.COMPLETED,
        stage=RunStage.COMPLETED,
        updated_at=created,
        output_directory=str(root),
        completed_attempt_count=5,
        server_stopped=True,
        sampler_stopped=True,
    )
    _dump(root / "manifest.json", manifest)
    _dump(root / "hardware.json", _envelope(hardware))
    _dump(
        root / "runtime-inspection.json",
        _envelope(
            {
                "capabilities": capabilities.model_dump(mode="json"),
                "binary": binary_info.model_dump(mode="json"),
            }
        ),
    )
    _dump(root / "server-command.json", _envelope({"command": command.model_dump(mode="json")}))
    _dump(root / "model.json", _envelope(model_info))
    _dump(root / "workload.json", _envelope(workload_summary))
    _dump(
        root / "baseline-summary.json",
        {
            "schema_version": "1.0",
            "run_id": "synthetic-plan-baseline",
            "created_at": created.isoformat(),
            "status": "completed",
            "synthetic_fixture": True,
            "process": {
                "measured_phase_peak_rss_bytes": {
                    "value": 100_000_000,
                    "available": True,
                }
            },
        },
    )
    return root


def _mutate(path: Path, callback: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    callback(data)
    _dump(path, data)


def test_completed_synthetic_baseline_requires_explicit_opt_in(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    baseline = _make_baseline(tmp_path / "baseline", fake_binary, fake_model)
    with pytest.raises(BaselineReferenceError, match="allow-synthetic"):
        load_baseline_input(baseline, allow_synthetic=False, allow_runtime_change=False)
    loaded = load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=False)
    assert loaded.baseline is not None
    assert loaded.baseline.synthetic_fixture is True
    assert loaded.baseline_runtime.threads == 4
    assert loaded.baseline_runtime.context_size == 4096


@pytest.mark.parametrize("status", ["partial", "interrupted"])
def test_incomplete_baseline_is_rejected(
    tmp_path: Path, fake_binary: Path, fake_model: Path, status: str
) -> None:
    baseline = _make_baseline(tmp_path / status, fake_binary, fake_model)
    _mutate(baseline / "manifest.json", lambda data: data.update({"status": status}))
    with pytest.raises(BaselineReferenceError, match="completed"):
        load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=False)


def test_missing_required_artifact_is_rejected(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    baseline = _make_baseline(tmp_path / "missing", fake_binary, fake_model)
    (baseline / "hardware.json").unlink()
    with pytest.raises(BaselineReferenceError, match=r"hardware\.json"):
        load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=False)


def test_unsupported_schema_is_rejected(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    baseline = _make_baseline(tmp_path / "schema", fake_binary, fake_model)
    _mutate(
        baseline / "manifest.json",
        lambda data: data.update({"schema_version": "999"}),
    )
    with pytest.raises(BaselineReferenceError, match="schema_version"):
        load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=False)


@pytest.mark.parametrize(
    ("artifact", "path", "field"),
    [
        ("model.json", ("data", "hash", "value"), "model_sha256"),
        ("workload.json", ("data", "sha256"), "workload_sha256"),
        (
            "runtime-inspection.json",
            ("data", "binary", "hash", "value"),
            "runtime_binary_sha256",
        ),
    ],
)
def test_hash_mismatches_are_detected(
    tmp_path: Path,
    fake_binary: Path,
    fake_model: Path,
    artifact: str,
    path: tuple[str, ...],
    field: str,
) -> None:
    baseline = _make_baseline(tmp_path / field, fake_binary, fake_model)

    def change(data: dict[str, object]) -> None:
        current: object = data
        for token in path[:-1]:
            assert isinstance(current, dict)
            current = current[token]
        assert isinstance(current, dict)
        current[path[-1]] = "0" * 64

    _mutate(baseline / artifact, change)
    with pytest.raises(ProvenanceMismatchError, match=field):
        load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=False)


def test_runtime_change_override_is_explicitly_recorded(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    baseline = _make_baseline(tmp_path / "override", fake_binary, fake_model)

    def change(data: dict[str, object]) -> None:
        data["data"]["binary"]["hash"]["value"] = "0" * 64

    _mutate(baseline / "runtime-inspection.json", change)
    loaded = load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=True)
    assert "runtime_binary_sha256" in loaded.overrides
    assert loaded.baseline is not None
    assert loaded.baseline.compatibility.classification.value == "compatible_with_warnings"


def test_core_and_feature_difference_is_compatible_with_warning(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    baseline = _make_baseline(tmp_path / "warning", fake_binary, fake_model)

    def change(data: dict[str, object]) -> None:
        data["data"]["physical_cores"] = 999
        data["data"]["features"]["sve"] = not data["data"]["features"]["sve"]

    _mutate(baseline / "hardware.json", change)
    loaded = load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=False)
    assert loaded.baseline is not None
    fields = {item.field for item in loaded.baseline.compatibility.differences}
    assert {"physical_cores", "cpu_features"} <= fields


def test_available_memory_difference_is_compatible_with_warning(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    baseline = _make_baseline(tmp_path / "memory-warning", fake_binary, fake_model)

    def change_available_memory(data: dict[str, Any]) -> None:
        current = data["data"]["memory_available_bytes"]
        data["data"]["memory_available_bytes"] = (current or 0) + 4096

    _mutate(baseline / "hardware.json", change_available_memory)
    loaded = load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=False)
    assert loaded.baseline is not None
    fields = {item.field for item in loaded.baseline.compatibility.differences}
    assert "memory_available_bytes" in fields


def test_architecture_mismatch_is_incompatible(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    baseline = _make_baseline(tmp_path / "arch", fake_binary, fake_model)

    def change(data: dict[str, object]) -> None:
        data["data"]["architecture"] = "aarch64"
        data["data"]["is_arm64"] = True

    _mutate(baseline / "hardware.json", change)
    with pytest.raises(ProvenanceMismatchError, match="architecture"):
        load_baseline_input(baseline, allow_synthetic=True, allow_runtime_change=False)


def test_cli_plans_from_baseline_and_rejects_synthetic_without_opt_in(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    baseline = _make_baseline(tmp_path / "cli-baseline", fake_binary, fake_model)
    arguments = [
        "plan",
        "--baseline",
        str(baseline),
        "--goal",
        "balanced",
        "--output-dir",
        str(tmp_path / "cli-plan"),
    ]
    rejected = runner.invoke(app, arguments)
    assert rejected.exit_code == 1
    assert "allow-synthetic" in rejected.output
    accepted = runner.invoke(app, [*arguments, "--allow-synthetic"])
    assert accepted.exit_code == 0, accepted.output
    assert "Baseline available:     yes" in accepted.output
    assert "Synthetic planning fixture" in accepted.output

    (baseline / "manifest.json").write_text("{}\n", encoding="utf-8")
    validation = validate_plan_directory(tmp_path / "cli-plan")
    assert validation.valid is False
    assert any("manifest hash" in error for error in validation.errors)
