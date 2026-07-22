from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import psutil

from aarchtune.benchmark.system_metrics import ProcessMetricsSampler


def test_sampler_starts_streams_phase_and_joins(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    sampler = ProcessMetricsSampler(os.getpid(), path, "run", interval_seconds=0.05)
    sampler.start()
    sampler.set_phase("measured")
    assert sampler.wait_for_phase_sample("measured", timeout_seconds=1.0) is True
    sampler.stop()
    assert sampler.thread_alive is False
    assert sampler.sample_count >= 1
    assert '"phase": "measured"' in path.read_text(encoding="utf-8")
    summary = sampler.summary()
    assert summary.whole_run_peak_rss_bytes.available is True
    assert summary.measured_phase_peak_rss_bytes.available is True


def test_sampler_handles_process_disappearance(tmp_path: Path) -> None:
    process = subprocess.Popen(["sleep", "0.05"])
    sampler = ProcessMetricsSampler(
        process.pid, tmp_path / "disappears.jsonl", "run", interval_seconds=0.05
    )
    sampler.start()
    process.wait(timeout=1)
    time.sleep(0.08)
    sampler.stop()
    assert sampler.thread_alive is False
    assert sampler.summary().sample_count >= 0


def test_sampler_stop_is_safe_after_exceptional_process_loss(tmp_path: Path) -> None:
    sampler = ProcessMetricsSampler(999_999_999, tmp_path / "missing.jsonl", "run")
    sampler.start()
    time.sleep(0.02)
    sampler.stop()
    summary = sampler.summary()
    assert sampler.thread_alive is False
    assert summary.whole_run_peak_rss_bytes.available is False
    assert summary.sampling_errors


def test_sampler_aggregates_only_descendants_from_owned_root(tmp_path: Path) -> None:
    class FakeChild:
        def memory_info(self) -> object:
            return SimpleNamespace(rss=25)

    class FakeRoot:
        def children(self, *, recursive: bool) -> list[FakeChild]:
            assert recursive is True
            return [FakeChild()]

        def memory_info(self) -> object:
            return SimpleNamespace(rss=100, vms=1000)

        def cpu_times(self) -> object:
            return SimpleNamespace(user=1.0, system=0.5)

        def cpu_percent(self, interval: object = None) -> float:
            assert interval is None
            return 2.0

        def num_threads(self) -> int:
            return 3

    sampler = ProcessMetricsSampler(123, tmp_path / "tree.jsonl", "run")
    sampler.set_phase("measured")
    sample = sampler._sample(cast(psutil.Process, FakeRoot()))
    assert sample.pid == 123
    assert sample.rss_bytes == 100
    assert sample.aggregate_rss_bytes == 125
    assert sample.child_process_count == 1
