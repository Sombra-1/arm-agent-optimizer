from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from aarchtune.runtime.capabilities import ServerCapabilities
from aarchtune.runtime.client import execute_workload_task
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.process import LlamaServerProcess
from aarchtune.workload.evaluation import evaluate_workload
from aarchtune.workload.loader import load_workload
from aarchtune.workload.schema import ValidatorType


def _smoke_workload() -> object:
    repository = Path(__file__).resolve().parents[2]
    return load_workload(repository / "workloads/smoke-test.jsonl")


def _pid_is_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_one_workload_task_through_fake_server(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    workload = _smoke_workload()
    task = workload.tasks[0]

    with LlamaServerProcess(config_factory(), server_capabilities) as server:
        server.wait_until_ready()
        response = execute_workload_task(server.client, task)

    result = evaluate_workload(workload.model_copy(update={"tasks": [task]}), [response])
    assert result.task_pass_count == 1


def test_all_five_smoke_tasks_through_fake_server_and_process_gone(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    workload = _smoke_workload()
    server = LlamaServerProcess(config_factory(), server_capabilities)

    with server:
        server.wait_until_ready()
        pid = server.pid
        responses = [execute_workload_task(server.client, task) for task in workload.tasks]

    summary = evaluate_workload(workload, responses)
    assert summary.tasks_evaluated == 5
    assert summary.task_pass_count == 5
    assert summary.validator_pass_count == summary.total_validators
    assert pid is not None
    deadline = time.monotonic() + 1.0
    while not _pid_is_gone(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _pid_is_gone(pid)


@pytest.mark.parametrize(
    ("scenario", "timed_out"),
    [("slow-request", True), ("http-500", False)],
)
def test_runtime_failure_metadata_reaches_request_validator(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
    scenario: str,
    timed_out: bool,
) -> None:
    workload = _smoke_workload()
    task = workload.tasks[0]
    environment = {"FAKE_LLAMA_SCENARIO": scenario}
    if scenario == "slow-request":
        environment["FAKE_LLAMA_DELAY"] = "0.4"
    config = config_factory(
        request_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.1,
        extra_environment=environment,
    )
    server = LlamaServerProcess(config, server_capabilities)

    with server:
        server.wait_until_ready()
        pid = server.pid
        response = execute_workload_task(server.client, task)

    reduced_workload = workload.model_copy(update={"tasks": [task]})
    summary = evaluate_workload(reduced_workload, [response])
    request_result = next(
        result
        for result in summary.task_results[0].validator_results
        if result.validator is ValidatorType.REQUEST_SUCCEEDED
    )
    assert response.timed_out is timed_out
    assert request_result.passed is False
    assert summary.task_failure_count == 1
    assert pid is not None and _pid_is_gone(pid)
