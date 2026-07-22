from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from aarchtune.baseline.models import BaselineRunConfig, RunStatus
from aarchtune.baseline.runner import hash_file_streaming, run_baseline


def _workload() -> Path:
    return Path(__file__).resolve().parents[2] / "workloads/smoke-test.jsonl"


def _config(
    tmp_path: Path,
    fake_binary: Path,
    fake_model: Path,
    *,
    scenario: str = "healthy-with-timings",
    repetitions: int = 1,
    warmup: int = 1,
    **environment: str,
) -> BaselineRunConfig:
    fake_environment = {"FAKE_LLAMA_SCENARIO": scenario, **environment}
    return BaselineRunConfig(
        binary_path=fake_binary,
        model_path=fake_model,
        workload_path=_workload(),
        output_dir=tmp_path / scenario,
        repetitions=repetitions,
        warmup_requests=warmup,
        request_timeout_seconds=0.15,
        startup_timeout_seconds=2.0,
        shutdown_timeout_seconds=0.2,
        sample_interval_seconds=0.05,
        extra_environment=fake_environment,
    )


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_healthy_baseline_multiple_repetitions_and_provenance(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    result = run_baseline(_config(tmp_path, fake_binary, fake_model, repetitions=2, warmup=1))
    assert result.status is RunStatus.COMPLETED
    assert result.exit_code == 0
    assert result.summary is not None
    assert result.summary.synthetic_fixture is True
    assert result.summary.is_arm64 is False
    assert result.summary.benchmark.measured_attempts_completed == 10
    assert result.summary.quality.request_success_rate == 1.0
    assert result.summary.quality.task_attempt_success_rate == 1.0
    assert result.summary.benchmark.prompt_tokens_processed == 200
    assert result.summary.benchmark.completion_tokens_generated == 80
    assert result.summary.benchmark.server_prompt_tokens_per_second.mean == 2000
    assert result.summary.benchmark.requests_per_minute.available is True
    assert result.summary.process.whole_run_peak_rss_bytes.available is True
    assert result.summary.process.measured_phase_peak_rss_bytes.available is True
    assert set(result.summary.quality.per_category) == {
        "bounded_tool_planning",
        "contradiction_detection",
        "incident_classification",
        "recovery_action",
        "structured_summary",
    }
    assert result.summary.quality.per_validator_type
    assert result.summary.execution.warmup_task_ids == ["smoke-incident-001"]
    assert len(_jsonl(result.output_dir / "raw-attempts.jsonl")) == 10
    assert len(_jsonl(result.output_dir / "request-metrics.jsonl")) == 10
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["server_stopped"] is True
    assert manifest["sampler_stopped"] is True

    model_record = json.loads((result.output_dir / "model.json").read_text())
    expected_hash = hashlib.sha256(fake_model.read_bytes()).hexdigest()
    assert model_record["data"]["hash"]["value"] == expected_hash
    runtime_record = json.loads((result.output_dir / "runtime-inspection.json").read_text())
    assert runtime_record["data"]["binary"]["hash"]["completed"] is True
    assert runtime_record["data"]["exact_server_arguments"][0] == str(fake_binary)


def test_warmup_is_excluded_and_task_order_is_deterministic(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    result = run_baseline(_config(tmp_path, fake_binary, fake_model, warmup=3))
    assert result.summary is not None
    records = _jsonl(result.output_dir / "raw-attempts.jsonl")
    assert len(records) == 5
    assert [record["task_index"] for record in records] == [0, 1, 2, 3, 4]
    assert result.summary.execution.warmup_task_ids == [
        "smoke-incident-001",
        "smoke-recovery-001",
        "smoke-summary-001",
    ]


def test_missing_timing_fields_stay_unavailable(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    result = run_baseline(
        _config(tmp_path, fake_binary, fake_model, scenario="healthy-without-timings")
    )
    assert result.summary is not None
    assert result.summary.benchmark.server_prompt_tokens_per_second.count == 0
    measurement = _jsonl(result.output_dir / "request-metrics.jsonl")[0]
    tokens = measurement["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["server_generation_tokens_per_second"]["available"] is False
    assert tokens["time_to_first_token_seconds"]["value"] is None


def test_malformed_timing_fixture_does_not_fail_request(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    result = run_baseline(_config(tmp_path, fake_binary, fake_model, scenario="malformed-timings"))
    assert result.status is RunStatus.COMPLETED
    assert result.summary is not None
    assert result.summary.quality.request_success_rate == 1.0
    record = _jsonl(result.output_dir / "request-metrics.jsonl")[0]
    tokens = record["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["prompt_processing_seconds"]["available"] is False
    assert tokens["server_generation_tokens_per_second"]["available"] is False


def test_process_memory_growth_fixture_produces_process_evidence(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    result = run_baseline(
        _config(tmp_path, fake_binary, fake_model, scenario="process-memory-growth")
    )
    assert result.summary is not None
    assert result.summary.process.sample_count >= 2
    assert result.summary.process.measured_phase_peak_rss_bytes.available is True


def test_quality_failure_is_a_completed_baseline(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    result = run_baseline(_config(tmp_path, fake_binary, fake_model, scenario="mixed-task-quality"))
    assert result.exit_code == 0
    assert result.status is RunStatus.COMPLETED
    assert result.summary is not None
    assert result.summary.quality.request_success_rate == 1.0
    assert result.summary.quality.task_attempt_success_rate == pytest.approx(0.8)


def test_partial_http_failure_is_recorded_but_run_continues(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    result = run_baseline(
        _config(tmp_path, fake_binary, fake_model, scenario="partial-http-failure")
    )
    assert result.exit_code == 0
    assert result.summary is not None
    assert result.summary.benchmark.http_failure_count == 1
    assert result.summary.quality.request_success_count == 4
    assert result.summary.quality.failed_task_attempts >= 1


def test_timeout_is_persisted_and_runtime_stays_distinct_from_quality(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    config = _config(
        tmp_path,
        fake_binary,
        fake_model,
        scenario="slow-request",
        warmup=0,
        FAKE_LLAMA_DELAY="0.3",
    ).model_copy(update={"maximum_consecutive_infrastructure_failures": 10})
    result = run_baseline(config)
    assert result.status is RunStatus.COMPLETED
    assert result.summary is not None
    assert result.summary.benchmark.timeout_count == 5
    assert result.summary.quality.request_success_rate == 0.0
    assert result.summary.quality.task_attempt_success_rate == 0.0


def test_server_exit_produces_partial_artifacts_and_cleanup(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    pid_file = tmp_path / "partial.pid"
    result = run_baseline(
        _config(
            tmp_path,
            fake_binary,
            fake_model,
            scenario="server-exits-mid-run",
            warmup=0,
            FAKE_LLAMA_PID_FILE=str(pid_file),
            FAKE_LLAMA_EXIT_AFTER="2",
        )
    )
    assert result.status is RunStatus.PARTIAL
    assert result.exit_code == 3
    assert result.failure is not None
    assert result.failure.completed_attempt_count == 3
    assert (result.output_dir / "failure.json").is_file()
    assert (result.output_dir / "quality-summary.json").is_file()
    assert len(_jsonl(result.output_dir / "raw-attempts.jsonl")) == 3
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_simulated_interruption_preserves_manifest_and_cleans_up(
    tmp_path: Path,
    fake_binary: Path,
    fake_model: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_file = tmp_path / "interrupted.pid"

    def interrupt(**_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("aarchtune.baseline.runner.measure_task_attempt", interrupt)
    result = run_baseline(
        _config(
            tmp_path,
            fake_binary,
            fake_model,
            warmup=0,
            FAKE_LLAMA_PID_FILE=str(pid_file),
        )
    )
    assert result.status is RunStatus.INTERRUPTED
    assert result.exit_code == 3
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert manifest["status"] == "interrupted"
    assert manifest["server_stopped"] is True
    assert manifest["sampler_stopped"] is True
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_streaming_hash_reads_in_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "large.gguf"
    path.write_bytes(b"x" * 25)
    read_sizes: list[int] = []
    original_open: Callable[..., object] = Path.open

    class TrackingReader:
        def __init__(self, wrapped: object) -> None:
            self.wrapped = wrapped

        def __enter__(self) -> TrackingReader:
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self.wrapped.__exit__(*args)

        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            return self.wrapped.read(size)

    def tracked_open(target: Path, *args: object, **kwargs: object) -> TrackingReader:
        return TrackingReader(original_open(target, *args, **kwargs))

    monkeypatch.setattr(Path, "open", tracked_open)
    assert hash_file_streaming(path, chunk_bytes=8) == hashlib.sha256(b"x" * 25).hexdigest()
    assert read_sizes == [8, 8, 8, 8, 8]
