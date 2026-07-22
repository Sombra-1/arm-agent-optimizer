from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable

import pytest
from pytest import MonkeyPatch

from aarchtune.runtime.capabilities import ServerCapabilities
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.errors import PortInUseError, ReadinessTimeoutError, ServerExitedError
from aarchtune.runtime.process import LlamaServerProcess


def _assert_pid_gone(pid: int) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"fake llama-server PID {pid} remained alive")


def test_healthy_startup_and_graceful_shutdown(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    server = LlamaServerProcess(config_factory(), server_capabilities)

    with server:
        readiness = server.wait_until_ready()
        pid = server.pid
        assert readiness.ready is True
        assert readiness.method == "health_endpoint"
        assert server.is_running is True

    assert pid is not None
    assert server.shutdown_result is not None
    assert server.shutdown_result.graceful is True
    assert server.shutdown_result.forced is False
    _assert_pid_gone(pid)


def test_delayed_startup(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    config = config_factory(
        startup_timeout_seconds=2.0,
        extra_environment={"FAKE_LLAMA_SCENARIO": "startup-delay", "FAKE_LLAMA_DELAY": "0.25"},
    )
    with LlamaServerProcess(config, server_capabilities) as server:
        readiness = server.wait_until_ready()

    assert readiness.ready is True
    assert readiness.elapsed_seconds >= 0.2


@pytest.mark.parametrize("scenario", ["early-exit", "startup-failure"])
def test_early_exit_and_startup_failure_cleanup(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
    scenario: str,
) -> None:
    config = config_factory(extra_environment={"FAKE_LLAMA_SCENARIO": scenario})
    server = LlamaServerProcess(config, server_capabilities)

    with pytest.raises(ServerExitedError) as raised, server:
        pid = server.pid
        server.wait_until_ready()

    assert scenario.replace("-", " ") in raised.value.log_tail
    assert pid is not None
    _assert_pid_gone(pid)


def test_readiness_timeout_cleans_process(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    config = config_factory(
        startup_timeout_seconds=0.25,
        extra_environment={"FAKE_LLAMA_SCENARIO": "no-readiness"},
    )
    server = LlamaServerProcess(config, server_capabilities)

    with pytest.raises(ReadinessTimeoutError, match="Last probe"), server:
        pid = server.pid
        server.wait_until_ready()

    assert pid is not None
    assert server.shutdown_result is not None
    _assert_pid_gone(pid)


def test_occupied_port_does_not_start_or_kill_owner(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupying_socket:
        occupying_socket.bind(("127.0.0.1", 0))
        occupying_socket.listen()
        port = occupying_socket.getsockname()[1]
        server = LlamaServerProcess(config_factory(port=port), server_capabilities)

        with pytest.raises(PortInUseError, match="already in use"):
            server.start()

        assert server.pid is None
        occupying_socket.getsockname()


def test_port_race_bind_failure_is_actionable_and_owner_survives(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
    monkeypatch: MonkeyPatch,
) -> None:
    from aarchtune.runtime import process as process_module

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupying_socket:
        occupying_socket.bind(("127.0.0.1", 0))
        occupying_socket.listen()
        port = occupying_socket.getsockname()[1]
        monkeypatch.setattr(process_module, "is_port_available", lambda host, value: True)
        server = LlamaServerProcess(config_factory(port=port), server_capabilities)

        with pytest.raises(PortInUseError, match="already in use"), server:
            pid = server.pid
            server.wait_until_ready()

        occupying_socket.getsockname()
    assert pid is not None
    _assert_pid_gone(pid)


def test_stop_is_idempotent(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    server = LlamaServerProcess(config_factory(), server_capabilities).start()
    server.wait_until_ready()
    pid = server.pid

    first = server.stop()
    second = server.stop()

    assert first is second
    assert pid is not None
    _assert_pid_gone(pid)


def test_context_exception_cleans_process(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    server = LlamaServerProcess(config_factory(), server_capabilities)

    with pytest.raises(RuntimeError, match="synthetic context error"), server:
        server.wait_until_ready()
        pid = server.pid
        raise RuntimeError("synthetic context error")

    assert pid is not None
    _assert_pid_gone(pid)


def test_ignored_sigterm_escalates_only_owned_group(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    config = config_factory(
        shutdown_timeout_seconds=0.1,
        extra_environment={"FAKE_LLAMA_SCENARIO": "ignore-term"},
    )
    server = LlamaServerProcess(config, server_capabilities)

    with server:
        server.wait_until_ready()
        pid = server.pid

    assert pid is not None
    assert server.shutdown_result is not None
    assert server.shutdown_result.graceful is False
    assert server.shutdown_result.forced is True
    _assert_pid_gone(pid)


def test_bounded_logs_retain_marker_and_diagnostic_tail(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    config = config_factory(
        maximum_log_bytes=512,
        extra_environment={"FAKE_LLAMA_SCENARIO": "log-flood"},
    )
    server = LlamaServerProcess(config, server_capabilities)

    with server:
        server.wait_until_ready()
        time.sleep(0.05)

    assert server.logs_truncated is True
    assert len(server.log_text.encode()) <= 512
    assert "log truncated" in server.log_text
    assert "useful diagnostic tail" in server.log_text


def test_secret_like_values_are_redacted_from_logs(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    config = config_factory(
        extra_environment={
            "FAKE_LLAMA_SCENARIO": "secret-log",
            "API_TOKEN": "never-print-this",
        }
    )
    server = LlamaServerProcess(config, server_capabilities)

    with server:
        server.wait_until_ready()

    assert "never-print-this" not in server.log_text
    assert "API_TOKEN=<redacted>" in server.log_text
