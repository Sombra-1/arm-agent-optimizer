from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = ROOT / "scripts/model_probe.py"

CHILD_SOURCE = """#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

behavior = os.environ.get("PROBE_TEST_BEHAVIOR", "exit_zero")
if behavior == "exit_zero":
    print("PRIVATE GENERATED OUTPUT")
    print(os.environ.get("PRIVATE_PROBE_PATH", ""), file=sys.stderr)
    raise SystemExit(0)
if behavior == "exit_nonzero":
    raise SystemExit(7)
if behavior == "exit_143":
    raise SystemExit(143)
if behavior == "wait":
    while True:
        time.sleep(0.05)
if behavior == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(0.05)
if behavior == "increment_oom":
    events = Path(os.environ["PROBE_TEST_MEMORY_EVENTS"])
    events.write_text("low 0\\nhigh 0\\nmax 0\\noom 1\\noom_kill 1\\noom_group_kill 0\\n")
    raise SystemExit(137)
if behavior == "leave_group_child":
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
        ]
    )
    raise SystemExit(0)
raise SystemExit(64)
"""


def _fake_cgroup(tmp_path: Path) -> Path:
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "memory.events").write_text(
        "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n",
        encoding="ascii",
    )
    (root / "memory.current").write_text("1024\n", encoding="ascii")
    (root / "memory.peak").write_text("2048\n", encoding="ascii")
    (root / "memory.max").write_text("4096\n", encoding="ascii")
    return root


def _setup(tmp_path: Path) -> tuple[list[str], dict[str, str], Path, Path, Path]:
    child = tmp_path / "fake-llama-cli"
    child.write_text(CHILD_SOURCE, encoding="utf-8")
    child.chmod(0o755)
    private = tmp_path / "private runner path"
    private.mkdir()
    model = private / "private-model.gguf"
    model.write_bytes(b"model")
    evidence = tmp_path / "public-evidence"
    result = evidence / "model-probe-result.json"
    resources = evidence / "probe-resource-evidence.jsonl"
    cgroup = _fake_cgroup(tmp_path)
    command = [
        sys.executable,
        str(SUPERVISOR),
        "--binary",
        str(child),
        "--model",
        str(model),
        "--runner-temp",
        str(private),
        "--result",
        str(result),
        "--resource-evidence",
        str(resources),
        "--timeout-seconds",
        "0.4",
        "--grace-seconds",
        "0.2",
        "--sample-interval-seconds",
        "0.02",
        "--cgroup-root",
        str(cgroup),
        "--",
        "--prompt",
        "PRIVATE PROMPT",
        "--n-predict",
        "8",
    ]
    environment = {
        **os.environ,
        "PRIVATE_PROBE_PATH": str(private),
        "PROBE_TEST_MEMORY_EVENTS": str(cgroup / "memory.events"),
    }
    return command, environment, result, resources, private


def _run(
    tmp_path: Path,
    behavior: str,
    *,
    timeout_seconds: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], Path, Path]:
    command, environment, result, resources, private = _setup(tmp_path)
    environment["PROBE_TEST_BEHAVIOR"] = behavior
    if timeout_seconds is not None:
        index = command.index("--timeout-seconds") + 1
        command[index] = timeout_seconds
    completed = subprocess.run(
        command,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return completed, json.loads(result.read_text(encoding="utf-8")), resources, private


def _start(tmp_path: Path, behavior: str) -> tuple[subprocess.Popen[str], Path, Path]:
    command, environment, result, _resources, private = _setup(tmp_path)
    environment["PROBE_TEST_BEHAVIOR"] = behavior
    process = subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pgid_file = private / "aarchtune-probe.pgid"
    deadline = time.monotonic() + 3
    while not pgid_file.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pgid_file.exists()
    return process, result, pgid_file


def test_child_exit_zero_is_model_loaded(tmp_path: Path) -> None:
    completed, result, resources, _private = _run(tmp_path, "exit_zero")
    assert completed.returncode == 0
    assert result["classification"] == "model_loaded"
    assert result["child_return_code"] == 0
    assert result["child_terminating_signal"] is None
    assert result["evidence_complete"] is True
    assert len(resources.read_text(encoding="utf-8").splitlines()) >= 2


def test_child_nonzero_is_controlled_probe_failure(tmp_path: Path) -> None:
    completed, result, _resources, _private = _run(tmp_path, "exit_nonzero")
    assert completed.returncode == 0
    assert result["classification"] == "model_probe_failed"
    assert result["outcome"] == "quality_evidence_incomplete"
    assert result["child_return_code"] == 7


def test_child_receives_sigterm_without_oom_inference(tmp_path: Path) -> None:
    process, result_path, pgid_file = _start(tmp_path, "wait")
    os.killpg(int(pgid_file.read_text(encoding="ascii")), signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)
    assert (stdout, stderr) == ("", "")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert process.returncode == 0
    assert result["classification"] == "probe_terminated_by_signal"
    assert result["outcome"] == "quality_evidence_incomplete"
    assert result["child_terminating_signal"] == signal.SIGTERM
    assert result["oom_evidence"] is False


def test_timeout_terminates_child_group(tmp_path: Path) -> None:
    completed, result, _resources, _private = _run(tmp_path, "wait")
    assert completed.returncode == 0
    assert result["classification"] == "probe_timeout"
    assert result["timed_out"] is True
    assert result["term_sent"] is True
    assert result["kill_sent"] is False
    assert result["child_group_cleaned"] is True


def test_timeout_escalates_to_sigkill_when_child_ignores_sigterm(tmp_path: Path) -> None:
    completed, result, _resources, _private = _run(tmp_path, "ignore_term")
    assert completed.returncode == 0
    assert result["classification"] == "probe_timeout"
    assert result["term_sent"] is True
    assert result["kill_sent"] is True
    assert result["child_terminating_signal"] == signal.SIGKILL
    assert result["child_group_cleaned"] is True


def test_supervisor_sigterm_writes_incomplete_checkpoint_and_cleans_child(
    tmp_path: Path,
) -> None:
    process, result_path, _pgid_file = _start(tmp_path, "wait")
    os.kill(process.pid, signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)
    assert (stdout, stderr) == ("", "")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert process.returncode == 75
    assert result["classification"] == "probe_terminated_by_signal"
    assert result["supervisor_signal"] == signal.SIGTERM
    assert result["child_group_cleaned"] is True
    assert result["evidence_complete"] is True


def test_lingering_child_group_is_cleaned_without_signaling_supervisor(
    tmp_path: Path,
) -> None:
    completed, result, _resources, _private = _run(tmp_path, "leave_group_child")
    assert completed.returncode == 0
    assert result["classification"] == "model_loaded"
    assert result["term_sent"] is True
    assert result["child_group_cleaned"] is True
    assert result["supervisor_signal"] is None


def test_oom_counter_increase_is_resource_incompatible(tmp_path: Path) -> None:
    completed, result, _resources, _private = _run(tmp_path, "increment_oom")
    assert completed.returncode == 0
    assert result["classification"] == "resource_incompatible"
    assert result["outcome"] == "resource_incompatible"
    assert result["oom_evidence"] is True
    assert result["oom_counter_delta"]["oom"] == 1
    assert result["oom_counter_delta"]["oom_kill"] == 1


def test_exit_143_without_oom_proof_remains_incomplete(tmp_path: Path) -> None:
    completed, result, _resources, _private = _run(tmp_path, "exit_143")
    assert completed.returncode == 0
    assert result["classification"] == "probe_terminated_by_signal"
    assert result["outcome"] == "quality_evidence_incomplete"
    assert result["child_return_code"] == 143
    assert result["child_terminating_signal"] is None
    assert result["signal_style_exit_code"] == signal.SIGTERM
    assert result["oom_evidence"] is False


def test_generated_output_and_private_paths_are_excluded_from_evidence(
    tmp_path: Path,
) -> None:
    completed, result, resources, private = _run(tmp_path, "exit_zero")
    assert completed.stdout == ""
    assert completed.stderr == ""
    serialized = json.dumps(result) + resources.read_text(encoding="utf-8")
    assert "PRIVATE GENERATED OUTPUT" not in serialized
    assert "PRIVATE PROMPT" not in serialized
    assert str(private) not in serialized
    assert "--model" not in serialized
    assert result["generated_output_published"] is False
    assert result["command_arguments_published"] is False
    assert (private / "aarchtune-model-probe.stdout").read_text() == ("PRIVATE GENERATED OUTPUT\n")


@pytest.mark.parametrize("option", ["--timeout-seconds", "--grace-seconds"])
def test_timeout_controls_require_positive_values(tmp_path: Path, option: str) -> None:
    command, environment, _result, _resources, _private = _setup(tmp_path)
    command[command.index(option) + 1] = "0"
    completed = subprocess.run(
        command,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
