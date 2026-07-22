from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from aarchtune.optimization.models import SearchPlan
from aarchtune.runtime.one_shot import run_one_shot
from aarchtune.screening.command import build_bench_command
from aarchtune.screening.models import LlamaBenchCapabilities, ScreeningScenario
from aarchtune.screening.signatures import build_signatures


def _command(
    screen_plan_dir: Path,
    capabilities: LlamaBenchCapabilities,
    model: Path,
):
    plan = SearchPlan.model_validate_json((screen_plan_dir / "search-plan.json").read_text())
    signature = build_signatures(plan.candidates, capabilities)[0][0]
    return build_bench_command(
        capabilities,
        model,
        signature,
        ScreeningScenario(id="decode", prompt_tokens=0, generation_tokens=16),
        1,
    )


def _run(
    tmp_path: Path,
    command,
    scenario: str,
    **updates: object,
):
    values = {
        "timeout_seconds": 2.0,
        "shutdown_timeout_seconds": 0.2,
        "sample_interval_seconds": 0.05,
        "maximum_log_bytes": 4096,
    }
    values.update(updates)
    return run_one_shot(
        command,
        invocation_id=f"inv-{scenario}",
        stdout_path=tmp_path / scenario / "stdout.jsonl",
        stderr_path=tmp_path / scenario / "stderr.log",
        samples_path=tmp_path / scenario / "samples.jsonl",
        extra_environment={
            "FAKE_LLAMA_BENCH_SCENARIO": scenario,
            "FAKE_LLAMA_BENCH_DELAY": "2",
        },
        **values,
    )


def _assert_gone(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"benchmark process {pid} remained alive")


def test_healthy_raw_streaming_and_sampler_cleanup(
    tmp_path: Path,
    screen_plan_dir: Path,
    bench_capabilities: LlamaBenchCapabilities,
    fake_model: Path,
) -> None:
    result = _run(
        tmp_path,
        _command(screen_plan_dir, bench_capabilities, fake_model),
        "healthy-jsonl",
    )
    assert result.exit_code == 0
    assert result.stdout_bytes > 0
    assert Path(result.stdout_path).read_bytes().startswith(b"{")
    assert result.process_stopped is True
    assert result.sampler_stopped is True
    assert result.pid is not None
    _assert_gone(result.pid)


def test_nonzero_exit_is_preserved(
    tmp_path: Path,
    screen_plan_dir: Path,
    bench_capabilities: LlamaBenchCapabilities,
    fake_model: Path,
) -> None:
    result = _run(
        tmp_path,
        _command(screen_plan_dir, bench_capabilities, fake_model),
        "nonzero-exit",
    )
    assert result.exit_code == 7
    assert "synthetic benchmark failure" in Path(result.stderr_path).read_text()
    assert result.pid is not None
    _assert_gone(result.pid)


@pytest.mark.parametrize("scenario", ["timeout", "ignore-term"])
def test_timeout_and_forced_cleanup(
    tmp_path: Path,
    screen_plan_dir: Path,
    bench_capabilities: LlamaBenchCapabilities,
    fake_model: Path,
    scenario: str,
) -> None:
    result = _run(
        tmp_path,
        _command(screen_plan_dir, bench_capabilities, fake_model),
        scenario,
        timeout_seconds=0.1,
        shutdown_timeout_seconds=0.1,
    )
    assert result.timed_out is True
    assert result.process_stopped is True
    assert result.sampler_stopped is True
    assert result.forced_termination is (scenario == "ignore-term")
    assert result.pid is not None
    _assert_gone(result.pid)


def test_logs_are_bounded_with_truncation_marker(
    tmp_path: Path,
    screen_plan_dir: Path,
    bench_capabilities: LlamaBenchCapabilities,
    fake_model: Path,
) -> None:
    result = _run(
        tmp_path,
        _command(screen_plan_dir, bench_capabilities, fake_model),
        "log-flood",
    )
    log = Path(result.stderr_path).read_text()
    assert result.stderr_truncated is True
    assert "log truncated" in log
    assert len(log.encode()) <= 4096


def test_memory_growth_produces_optional_owned_process_summary(
    tmp_path: Path,
    screen_plan_dir: Path,
    bench_capabilities: LlamaBenchCapabilities,
    fake_model: Path,
) -> None:
    result = _run(
        tmp_path,
        _command(screen_plan_dir, bench_capabilities, fake_model),
        "memory-growth",
    )
    assert result.process_summary.sample_count >= 1
    assert result.process_summary.whole_run_peak_rss_bytes.available is True
