from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from aarchtune.runtime.capabilities import ServerCapabilities
from aarchtune.runtime.client import ClientFailureKind, LlamaServerClient, execute_workload_task
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.process import LlamaServerProcess, select_loopback_port
from aarchtune.workload.loader import load_workload


def _smoke_task(index: int = 0) -> object:
    repository = Path(__file__).resolve().parents[2]
    return load_workload(repository / "workloads/smoke-test.jsonl").tasks[index]


def test_health_models_metrics_and_chat_success(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    with LlamaServerProcess(config_factory(), server_capabilities) as server:
        server.wait_until_ready()
        health = server.client.get_readiness("/health", timeout_seconds=0.5)
        models = server.client.get_models()
        metrics = server.client.get_metrics()
        response = execute_workload_task(server.client, _smoke_task())

    assert health.succeeded is True
    assert health.status_code == 200
    assert models.succeeded is True
    assert isinstance(models.json_data, dict)
    assert metrics.succeeded is True
    assert "fake_requests_total" in (metrics.text or "")
    assert response.request_succeeded is True
    assert response.task_id == "smoke-incident-001"
    assert json.loads(response.text)["category"] == "memory_leak"


@pytest.mark.parametrize(
    ("scenario", "kind", "error_text", "timed_out"),
    [
        ("http-500", ClientFailureKind.SERVER_ERROR, "server_error", False),
        ("invalid-json", ClientFailureKind.INVALID_JSON, "invalid_json", False),
        (
            "missing-content",
            ClientFailureKind.MISSING_CONTENT,
            "missing_completion_content",
            False,
        ),
        ("slow-request", ClientFailureKind.REQUEST_TIMEOUT, "request_timeout", True),
        (
            "large-response",
            ClientFailureKind.RESPONSE_TOO_LARGE,
            "response_too_large",
            False,
        ),
    ],
)
def test_chat_failure_mapping(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
    scenario: str,
    kind: ClientFailureKind,
    error_text: str,
    timed_out: bool,
) -> None:
    environment = {"FAKE_LLAMA_SCENARIO": scenario}
    if scenario == "slow-request":
        environment["FAKE_LLAMA_DELAY"] = "0.5"
    config = config_factory(
        request_timeout_seconds=0.1,
        shutdown_timeout_seconds=0.1,
        extra_environment=environment,
    )
    with LlamaServerProcess(config, server_capabilities) as server:
        server.wait_until_ready()
        response = execute_workload_task(server.client, _smoke_task())
        pid = server.pid

    assert response.request_succeeded is False
    assert response.timed_out is timed_out
    assert error_text in (response.error or "")
    if kind is ClientFailureKind.SERVER_ERROR:
        assert response.status_code == 500
    assert pid is not None
    assert server.shutdown_result is not None and server.shutdown_result.stopped


def test_connection_refused_is_distinct() -> None:
    port = select_loopback_port("127.0.0.1")
    with LlamaServerClient("127.0.0.1", port, request_timeout_seconds=0.1) as client:
        result = client.get_readiness("/health", timeout_seconds=0.1)

    assert result.succeeded is False
    assert result.error_kind is ClientFailureKind.CONNECTION_FAILURE
    assert "connection_failure" in (result.error or "")


def test_http_non_2xx_is_distinct_from_server_error(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    with LlamaServerProcess(config_factory(), server_capabilities) as server:
        server.wait_until_ready()
        result = server.client.get_readiness("/missing", timeout_seconds=0.5)

    assert result.error_kind is ClientFailureKind.HTTP_ERROR
    assert result.status_code == 404


def test_generation_parameters_and_non_streaming_are_preserved(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
    tmp_path: Path,
) -> None:
    request_file = tmp_path / "request.json"
    config = config_factory(
        extra_environment={
            "FAKE_LLAMA_SCENARIO": "echo-request",
            "FAKE_LLAMA_REQUEST_FILE": str(request_file),
        }
    )
    task = _smoke_task()
    with LlamaServerProcess(config, server_capabilities) as server:
        server.wait_until_ready()
        response = execute_workload_task(server.client, task)

    payload = json.loads(request_file.read_text(encoding="utf-8"))
    assert payload["temperature"] == task.generation.temperature
    assert payload["max_tokens"] == task.generation.max_tokens
    assert payload["seed"] == task.generation.seed
    assert payload["stream"] is False
    assert payload["messages"] == [message.model_dump(mode="json") for message in task.messages]
    assert response.task_id == task.id
