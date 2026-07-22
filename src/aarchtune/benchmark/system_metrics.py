"""Bounded psutil sampling of only the owned server process tree."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Literal

import psutil

from aarchtune.benchmark.models import (
    OptionalMetric,
    ProcessMetricsSummary,
    ProcessSample,
    unavailable_metric,
)

Phase = Literal["startup", "warmup", "measured", "shutdown"]
MAX_RETAINED_SUMMARY_SAMPLES = 100_000


def _available(value: int | float) -> OptionalMetric:
    return OptionalMetric(value=value, available=True, source="process_sampled")


class ProcessMetricsSampler:
    """Stream owned-process samples to JSONL while retaining only bounded aggregates."""

    def __init__(self, pid: int, path: Path, run_id: str, interval_seconds: float = 0.1) -> None:
        if not 0.05 <= interval_seconds <= 5.0:
            raise ValueError("sample interval must be between 0.05 and 5.0 seconds")
        self.pid = pid
        self.path = path
        self.run_id = run_id
        self.interval_seconds = interval_seconds
        self._phase: Phase = "startup"
        self._phase_lock = threading.Lock()
        self._sample_condition = threading.Condition()
        self._phase_sample_counts: dict[Phase, int] = {
            "startup": 0,
            "warmup": 0,
            "measured": 0,
            "shutdown": 0,
        }
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples: list[ProcessSample] = []
        self._errors: list[str] = []

    @property
    def thread_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def set_phase(self, phase: Phase) -> None:
        with self._phase_lock:
            self._phase = phase
        self._wake_event.set()

    def wait_for_phase_sample(self, phase: Phase, timeout_seconds: float) -> bool:
        """Wait boundedly until at least one streamed sample has the requested phase."""

        deadline = time.monotonic() + timeout_seconds
        with self._sample_condition:
            while self._phase_sample_counts[phase] == 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._sample_condition.wait(remaining)
            return True

    def start(self) -> None:
        if self._thread is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run,
            name=f"aarchtune-sampler-{self.pid}",
            daemon=True,
        )
        self._thread.start()

    def _sample(self, process: psutil.Process) -> ProcessSample:
        children = process.children(recursive=True)
        memory = process.memory_info()
        aggregate_rss = memory.rss
        live_children = 0
        for child in children:
            try:
                aggregate_rss += child.memory_info().rss
                live_children += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        cpu_times = process.cpu_times()
        with self._phase_lock:
            phase = self._phase
        return ProcessSample(
            run_id=self.run_id,
            timestamp=datetime.now(UTC),
            monotonic_ns=time.monotonic_ns(),
            phase=phase,
            pid=self.pid,
            rss_bytes=memory.rss,
            vms_bytes=memory.vms,
            cpu_percent=process.cpu_percent(interval=None),
            user_cpu_seconds=cpu_times.user,
            system_cpu_seconds=cpu_times.system,
            thread_count=process.num_threads(),
            child_process_count=live_children,
            aggregate_rss_bytes=aggregate_rss,
        )

    def _run(self) -> None:
        try:
            process = psutil.Process(self.pid)
            process.cpu_percent(interval=None)
            with self.path.open("a", encoding="utf-8") as output:
                while not self._stop_event.is_set():
                    try:
                        sample = self._sample(process)
                    except psutil.NoSuchProcess:
                        break
                    except (psutil.AccessDenied, OSError) as exc:
                        self._errors.append(f"{type(exc).__name__}: {exc}")
                    else:
                        output.write(
                            json.dumps(sample.model_dump(mode="json"), sort_keys=True) + "\n"
                        )
                        output.flush()
                        if len(self._samples) < MAX_RETAINED_SUMMARY_SAMPLES:
                            self._samples.append(sample)
                        elif not any("retention limit" in error for error in self._errors):
                            self._errors.append(
                                "In-memory sample retention limit reached; "
                                "JSONL streaming continued"
                            )
                        with self._sample_condition:
                            self._phase_sample_counts[sample.phase] += 1
                            self._sample_condition.notify_all()
                    self._wake_event.wait(self.interval_seconds)
                    self._wake_event.clear()
        except (psutil.NoSuchProcess, OSError) as exc:
            self._errors.append(f"{type(exc).__name__}: {exc}")

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_seconds * 3))
            if self._thread.is_alive():
                self._errors.append("Sampler thread did not stop before join timeout")

    def summary(self) -> ProcessMetricsSummary:
        samples = self._samples
        measured = [sample for sample in samples if sample.phase == "measured"]

        def maximum(source: list[ProcessSample], attribute: str, reason: str) -> OptionalMetric:
            if not source:
                return unavailable_metric(reason)
            return _available(max(getattr(sample, attribute) for sample in source))

        def mean(source: list[ProcessSample], attribute: str, reason: str) -> OptionalMetric:
            if not source:
                return unavailable_metric(reason)
            return _available(fmean(float(getattr(sample, attribute)) for sample in source))

        cpu_source = measured or samples
        first = samples[0] if samples else None
        last = samples[-1] if samples else None
        return ProcessMetricsSummary(
            run_id=self.run_id,
            sample_count=len(samples),
            whole_run_peak_rss_bytes=maximum(samples, "aggregate_rss_bytes", "No process samples"),
            measured_phase_peak_rss_bytes=maximum(
                measured, "aggregate_rss_bytes", "No measured-phase process samples"
            ),
            mean_measured_rss_bytes=mean(
                measured, "aggregate_rss_bytes", "No measured-phase process samples"
            ),
            peak_vms_bytes=maximum(samples, "vms_bytes", "No process samples"),
            mean_cpu_percent=mean(cpu_source, "cpu_percent", "No process samples"),
            peak_cpu_percent=maximum(cpu_source, "cpu_percent", "No process samples"),
            user_cpu_seconds_delta=(
                _available(max(0.0, last.user_cpu_seconds - first.user_cpu_seconds))
                if first and last
                else unavailable_metric("Fewer than one process sample")
            ),
            system_cpu_seconds_delta=(
                _available(max(0.0, last.system_cpu_seconds - first.system_cpu_seconds))
                if first and last
                else unavailable_metric("Fewer than one process sample")
            ),
            maximum_thread_count=maximum(samples, "thread_count", "No process samples"),
            maximum_child_process_count=maximum(
                samples, "child_process_count", "No process samples"
            ),
            sampling_errors=self._errors.copy(),
        )
