#!/usr/bin/env python3
"""Supervise a private, bounded llama-cli model-load probe."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, TextIO

CONTROLLED_SIGNAL_EXIT = 75
CONTROLLED_INTERNAL_EXIT = 70
CGROUP_FILES = ("memory.current", "memory.peak", "memory.max")
OOM_COUNTERS = ("oom", "oom_kill", "oom_group_kill")
STATE_CODES = {
    "R": 1,
    "S": 2,
    "D": 3,
    "Z": 4,
    "T": 5,
    "t": 6,
    "X": 7,
    "I": 8,
}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, raw = line.split(":", 1)
            match = re.fullmatch(r"\s*(\d+)\s+kB\s*", raw)
            if match:
                values[key] = int(match.group(1)) * 1024
    except (OSError, ValueError):
        return {}
    return values


def _read_numeric(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if value == "max":
        return -1
    try:
        return int(value)
    except ValueError:
        return None


def _memory_events(cgroup_root: Path) -> dict[str, int]:
    events: dict[str, int] = {}
    try:
        lines = (cgroup_root / "memory.events").read_text(encoding="ascii").splitlines()
    except OSError:
        return events
    for line in lines:
        fields = line.split()
        if len(fields) != 2 or re.fullmatch(r"[a-z_]+", fields[0]) is None:
            continue
        try:
            events[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return events


def _cgroup_values(cgroup_root: Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for filename in CGROUP_FILES:
        value = _read_numeric(cgroup_root / filename)
        if value is not None:
            values[filename.replace(".", "_") + "_bytes"] = value
    events = _memory_events(cgroup_root)
    if events:
        values["memory_events"] = events
    return values


def _child_status(pid: int) -> tuple[int | None, int | None]:
    try:
        fields = (Path("/proc") / str(pid) / "status").read_text(encoding="ascii").splitlines()
    except OSError:
        return None, None
    rss_bytes: int | None = None
    state_code: int | None = None
    for line in fields:
        if line.startswith("VmRSS:"):
            match = re.fullmatch(r"VmRSS:\s+(\d+)\s+kB", line)
            if match:
                rss_bytes = int(match.group(1)) * 1024
        elif line.startswith("State:"):
            state = line.removeprefix("State:").strip()[:1]
            state_code = STATE_CODES.get(state, 0)
    return rss_bytes, state_code


def _oom_deltas(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {
        counter: max(0, after.get(counter, 0) - before.get(counter, 0)) for counter in OOM_COUNTERS
    }


class ProbeSupervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.started = time.monotonic()
        self.child: subprocess.Popen[bytes] | None = None
        self.child_return_code: int | None = None
        self.supervisor_signal: int | None = None
        self.timed_out = False
        self.term_sent = False
        self.kill_sent = False
        self.child_group_cleaned = False
        self.pre_events: dict[str, int] = {}
        self.post_events: dict[str, int] | None = None
        self.resource_stream: TextIO | None = None

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def _result(self, *, evidence_complete: bool) -> dict[str, Any]:
        terminating_signal = (
            -self.child_return_code
            if self.child_return_code is not None and self.child_return_code < 0
            else None
        )
        signal_style_exit = (
            self.child_return_code - 128
            if self.child_return_code is not None and 129 <= self.child_return_code <= 192
            else None
        )
        deltas = (
            _oom_deltas(self.pre_events, self.post_events) if self.post_events is not None else None
        )
        oom_increase = deltas is not None and any(deltas.values())
        if oom_increase:
            classification = "resource_incompatible"
            outcome = "resource_incompatible"
        elif self.timed_out:
            classification = "probe_timeout"
            outcome = "quality_evidence_incomplete"
        elif (
            self.supervisor_signal in {signal.SIGINT, signal.SIGTERM}
            or terminating_signal == signal.SIGTERM
            or signal_style_exit == signal.SIGTERM
        ):
            classification = "probe_terminated_by_signal"
            outcome = "quality_evidence_incomplete"
        elif self.child_return_code == 0:
            classification = "model_loaded"
            outcome = "quality_evidence_incomplete"
        else:
            classification = "model_probe_failed"
            outcome = "quality_evidence_incomplete"
        return {
            "schema_version": 1,
            "classification": classification,
            "outcome": outcome,
            "evidence_complete": evidence_complete,
            "child_return_code": self.child_return_code,
            "child_terminating_signal": terminating_signal,
            "signal_style_exit_code": signal_style_exit,
            "supervisor_signal": self.supervisor_signal,
            "elapsed_ms": self.elapsed_ms(),
            "timeout_seconds": self.args.timeout_seconds,
            "grace_seconds": self.args.grace_seconds,
            "timed_out": self.timed_out,
            "term_sent": self.term_sent,
            "kill_sent": self.kill_sent,
            "child_group_cleaned": self.child_group_cleaned,
            "oom_counter_delta": deltas,
            "oom_evidence": oom_increase if deltas is not None else None,
            "generated_output_published": False,
            "command_arguments_published": False,
        }

    def write_result(self, *, evidence_complete: bool) -> None:
        _atomic_json(self.args.result, self._result(evidence_complete=evidence_complete))

    def _record(self, phase: int) -> None:
        if self.resource_stream is None:
            return
        meminfo = _meminfo()
        record: dict[str, Any] = {
            "phase": phase,
            "elapsed_ms": self.elapsed_ms(),
        }
        mappings = {
            "MemTotal": "system_ram_bytes",
            "MemAvailable": "mem_available_bytes",
            "SwapTotal": "swap_total_bytes",
            "SwapFree": "swap_free_bytes",
        }
        for source, destination in mappings.items():
            if source in meminfo:
                record[destination] = meminfo[source]
        if phase == 0:
            try:
                record["model_size_bytes"] = self.args.model.stat().st_size
            except OSError:
                record["model_size_bytes"] = -1
        if self.child is not None:
            rss, state_code = _child_status(self.child.pid)
            if rss is not None:
                record["child_rss_bytes"] = rss
            if state_code is not None:
                record["child_state_code"] = state_code
            record["child_running"] = int(self.child.poll() is None)
        record.update(_cgroup_values(self.args.cgroup_root))
        self.resource_stream.write(json.dumps(record, sort_keys=True) + "\n")
        self.resource_stream.flush()

    def _group_exists(self) -> bool:
        if self.child is None or self.child.pid == os.getpgrp():
            return False
        try:
            os.killpg(self.child.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _signal_group(self, signum: int) -> None:
        if self.child is None:
            return
        if self.child.pid == os.getpgrp():
            raise RuntimeError("refusing to signal supervisor process group")
        try:
            os.killpg(self.child.pid, signum)
        except ProcessLookupError:
            return
        if signum == signal.SIGTERM:
            self.term_sent = True
        elif signum == signal.SIGKILL:
            self.kill_sent = True

    def handle_signal(self, signum: int, _frame: object) -> None:
        if self.supervisor_signal is None:
            self.supervisor_signal = signum
            self._signal_group(signal.SIGTERM)
            self.write_result(evidence_complete=False)

    def _wait_for_group(self, deadline: float) -> None:
        while self._group_exists() and time.monotonic() < deadline:
            if self.child is not None:
                self.child.poll()
            self._record(1)
            time.sleep(min(self.args.sample_interval_seconds, 0.1))

    def _cleanup_group(self) -> None:
        if self._group_exists():
            self._signal_group(signal.SIGTERM)
            self._wait_for_group(time.monotonic() + self.args.grace_seconds)
        if self._group_exists():
            self._signal_group(signal.SIGKILL)
            self._wait_for_group(time.monotonic() + self.args.grace_seconds)
        if self.child is not None:
            try:
                self.child_return_code = self.child.wait(timeout=self.args.grace_seconds)
            except subprocess.TimeoutExpired:
                self._signal_group(signal.SIGKILL)
                try:
                    self.child_return_code = self.child.wait(timeout=self.args.grace_seconds)
                except subprocess.TimeoutExpired:
                    self.child_return_code = self.child.poll()
        self.child_group_cleaned = not self._group_exists()

    def run(self) -> int:
        self.args.runner_temp.mkdir(parents=True, exist_ok=True)
        self.args.resource_evidence.parent.mkdir(parents=True, exist_ok=True)
        stdout_path = self.args.runner_temp / "aarchtune-model-probe.stdout"
        stderr_path = self.args.runner_temp / "aarchtune-model-probe.stderr"
        self.resource_stream = self.args.resource_evidence.open("w", encoding="utf-8")
        self.pre_events = _memory_events(self.args.cgroup_root)
        self._record(0)
        signal.signal(signal.SIGINT, self.handle_signal)
        signal.signal(signal.SIGTERM, self.handle_signal)
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                command = [
                    str(self.args.binary),
                    "--model",
                    str(self.args.model),
                    *self.args.probe_arguments,
                ]
                self.child = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                (self.args.runner_temp / "aarchtune-probe.pgid").write_text(
                    f"{self.child.pid}\n",
                    encoding="ascii",
                )
                deadline = time.monotonic() + self.args.timeout_seconds
                while self.child.poll() is None:
                    self._record(1)
                    if self.supervisor_signal is not None:
                        break
                    if time.monotonic() >= deadline:
                        self.timed_out = True
                        self._signal_group(signal.SIGTERM)
                        break
                    time.sleep(self.args.sample_interval_seconds)
                self.child_return_code = self.child.poll()
                self._cleanup_group()
        except Exception:
            self._cleanup_group()
            self.post_events = _memory_events(self.args.cgroup_root)
            self._record(2)
            self.write_result(evidence_complete=True)
            return CONTROLLED_INTERNAL_EXIT
        finally:
            if self.resource_stream is not None:
                self.resource_stream.close()
                self.resource_stream = None
        self.post_events = _memory_events(self.args.cgroup_root)
        with self.args.resource_evidence.open("a", encoding="utf-8") as resource_stream:
            self.resource_stream = resource_stream
            self._record(2)
            self.resource_stream = None
        self.write_result(evidence_complete=True)
        if self.child_group_cleaned:
            (self.args.runner_temp / "aarchtune-probe.pgid").unlink(missing_ok=True)
        return CONTROLLED_SIGNAL_EXIT if self.supervisor_signal is not None else 0


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--runner-temp", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--resource-evidence", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=600.0)
    parser.add_argument("--grace-seconds", type=_positive_float, default=15.0)
    parser.add_argument("--sample-interval-seconds", type=_positive_float, default=1.0)
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    parser.add_argument("probe_arguments", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.probe_arguments[:1] == ["--"]:
        args.probe_arguments = args.probe_arguments[1:]
    return ProbeSupervisor(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
