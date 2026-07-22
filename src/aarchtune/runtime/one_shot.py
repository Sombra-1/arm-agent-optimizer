"""Safe bounded ownership of one non-interactive local process invocation."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from aarchtune.benchmark.system_metrics import ProcessMetricsSampler
from aarchtune.runtime.process import BoundedLogBuffer
from aarchtune.screening.models import BenchCommand, BenchExecutionResult


def run_one_shot(
    command: BenchCommand,
    *,
    invocation_id: str,
    stdout_path: Path,
    stderr_path: Path,
    samples_path: Path,
    timeout_seconds: float,
    shutdown_timeout_seconds: float,
    sample_interval_seconds: float,
    maximum_log_bytes: int,
    extra_environment: dict[str, str] | None = None,
) -> BenchExecutionResult:
    """Run exactly one argument list, streaming stdout and owning only its process group."""

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    started_ns = time.perf_counter_ns()
    process: subprocess.Popen[bytes] | None = None
    sampler: ProcessMetricsSampler | None = None
    stderr_buffer = BoundedLogBuffer(maximum_log_bytes)
    stderr_count = 0
    stderr_lock = threading.Lock()
    drain_thread: threading.Thread | None = None
    timed_out = False
    interrupted = False
    forced = False

    def drain(stream: BinaryIO) -> None:
        nonlocal stderr_count
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with stderr_lock:
                    stderr_count += len(chunk)
                stderr_buffer.append("stderr", chunk)
        finally:
            stream.close()

    def terminate_owned() -> None:
        nonlocal forced
        if process is None or process.poll() is not None:
            return
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=shutdown_timeout_seconds)
        except subprocess.TimeoutExpired:
            forced = True
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2.0)

    environment = os.environ.copy()
    if extra_environment:
        environment.update(extra_environment)
    with stdout_path.open("wb") as stdout_file:
        try:
            process = subprocess.Popen(
                command.arguments,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                env=environment,
            )
            if process.stderr is None:
                raise OSError("stderr capture pipe was not created")
            drain_thread = threading.Thread(
                target=drain,
                args=(process.stderr,),
                name=f"aarchtune-bench-stderr-{process.pid}",
                daemon=True,
            )
            drain_thread.start()
            sampler = ProcessMetricsSampler(
                process.pid,
                samples_path,
                invocation_id,
                interval_seconds=sample_interval_seconds,
            )
            sampler.set_phase("measured")
            sampler.start()
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_owned()
            except KeyboardInterrupt:
                interrupted = True
                terminate_owned()
        finally:
            if process is not None and process.poll() is None:
                terminate_owned()
            if sampler is not None:
                sampler.stop()
            if drain_thread is not None:
                drain_thread.join(timeout=2.0)
    stderr_path.write_text(stderr_buffer.text(), encoding="utf-8")
    finished_at = datetime.now(UTC)
    elapsed_ns = time.perf_counter_ns() - started_ns
    process_summary = (
        sampler.summary()
        if sampler is not None
        else ProcessMetricsSampler(0, samples_path, invocation_id).summary()
    )
    stdout_bytes = stdout_path.stat().st_size if stdout_path.exists() else 0
    return BenchExecutionResult(
        invocation_id=invocation_id,
        command=command,
        pid=process.pid if process is not None else None,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_ns=elapsed_ns,
        exit_code=process.poll() if process is not None else None,
        timed_out=timed_out,
        interrupted=interrupted,
        forced_termination=forced,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        process_samples_path=str(samples_path),
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_count,
        stderr_truncated=stderr_buffer.truncated,
        process_summary=process_summary,
        sampler_stopped=sampler is None or not sampler.thread_alive,
        process_stopped=process is None or process.poll() is not None,
    )
