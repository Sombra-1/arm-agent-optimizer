"""Exclusive, context-managed ownership of one local llama-server process."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict

from aarchtune.runtime.capabilities import (
    KleidiAIEvidence,
    ServerCapabilities,
    analyze_kleidiai_evidence,
)
from aarchtune.runtime.client import LlamaServerClient
from aarchtune.runtime.command import CommandBuildResult, build_llama_server_command
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.errors import (
    ConfigurationError,
    PortInUseError,
    ProcessShutdownError,
    ProcessStartError,
    ServerExitedError,
)
from aarchtune.runtime.readiness import ReadinessResult, wait_for_readiness
from aarchtune.runtime.redaction import redact_text

_TRUNCATION_MARKER = b"[... log truncated; recent tail follows ...]\n"


class ShutdownResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stopped: bool
    was_running: bool
    graceful: bool
    forced: bool
    return_code: int | None
    elapsed_seconds: float


class BoundedLogBuffer:
    """Thread-safe retained tail with a visible truncation marker."""

    def __init__(self, maximum_bytes: int) -> None:
        self.maximum_bytes = maximum_bytes
        self._buffer = bytearray()
        self._truncated = False
        self._lock = threading.Lock()

    def append(self, channel: str, data: bytes) -> None:
        tagged = f"[{channel}] ".encode() + data
        with self._lock:
            self._buffer.extend(tagged)
            if len(self._buffer) > self.maximum_bytes:
                self._truncated = True
                tail_size = max(0, self.maximum_bytes - len(_TRUNCATION_MARKER))
                tail = self._buffer[-tail_size:] if tail_size else b""
                self._buffer = bytearray(_TRUNCATION_MARKER + tail)

    @property
    def truncated(self) -> bool:
        with self._lock:
            return self._truncated

    def text(self) -> str:
        with self._lock:
            content = bytes(self._buffer)
        return redact_text(content.decode("utf-8", errors="replace"))

    def tail(self, maximum_characters: int = 4_000) -> str:
        return self.text()[-maximum_characters:]


def _socket_family(host: str) -> socket.AddressFamily:
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def is_port_available(host: str, port: int) -> bool:
    """Check bind availability without interacting with any occupying process."""

    try:
        with socket.socket(_socket_family(host), socket.SOCK_STREAM) as candidate:
            candidate.bind((host, port))
        return True
    except OSError:
        return False


def select_loopback_port(host: str) -> int:
    """Ask the kernel for a loopback port; closing creates a small unavoidable race."""

    if host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith("127."):
        raise ConfigurationError("automatic port selection is restricted to loopback hosts")
    bind_host = "127.0.0.1" if host == "localhost" else host
    with socket.socket(_socket_family(bind_host), socket.SOCK_STREAM) as candidate:
        candidate.bind((bind_host, 0))
        port = candidate.getsockname()[1]
    return int(port)


class LlamaServerProcess:
    """Own exactly one child process/session and all resources associated with it."""

    def __init__(self, config: LlamaServerConfig, capabilities: ServerCapabilities) -> None:
        self.original_config = config
        self.capabilities = capabilities
        self.config: LlamaServerConfig | None = None
        self.command: CommandBuildResult | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._client: LlamaServerClient | None = None
        self._threads: list[threading.Thread] = []
        self._logs = BoundedLogBuffer(config.maximum_log_bytes)
        self._shutdown_result: ShutdownResult | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def client(self) -> LlamaServerClient:
        if self._client is None:
            raise ProcessStartError("llama-server client is unavailable before start")
        return self._client

    @property
    def log_text(self) -> str:
        return self._logs.text()

    @property
    def logs_truncated(self) -> bool:
        return self._logs.truncated

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def shutdown_result(self) -> ShutdownResult | None:
        return self._shutdown_result

    def _drain(self, stream: BinaryIO, channel: str) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                self._logs.append(channel, chunk)
        finally:
            stream.close()

    def start(self) -> LlamaServerProcess:
        if self._process is not None:
            return self
        port = self.original_config.port
        if port is None:
            port = select_loopback_port(self.original_config.host)
        elif not is_port_available(self.original_config.host, port):
            raise PortInUseError(self.original_config.host, port)
        self.config = self.original_config.model_copy(update={"port": port})
        self.command = build_llama_server_command(self.config, self.capabilities)
        environment = os.environ.copy()
        environment.update(self.config.extra_environment)
        try:
            process = subprocess.Popen(
                self.command.arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                env=environment,
            )
        except OSError as exc:
            raise ProcessStartError(f"Could not start llama-server: {exc}") from exc
        self._process = process
        if process.stdout is None or process.stderr is None:
            self.stop()
            raise ProcessStartError("Could not capture llama-server stdout/stderr")
        for stream, channel in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            thread = threading.Thread(
                target=self._drain,
                args=(stream, channel),
                name=f"aarchtune-{channel}-{process.pid}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        self._client = LlamaServerClient(
            self.config.host,
            port,
            request_timeout_seconds=self.config.request_timeout_seconds,
        )
        return self

    def wait_until_ready(self) -> ReadinessResult:
        if self._process is None or self.config is None:
            raise ProcessStartError("llama-server has not been started")
        try:
            return wait_for_readiness(
                self.client,
                process_poll=self._process.poll,
                log_tail=self._logs.tail,
                endpoints=self.config.readiness_endpoints,
                startup_timeout_seconds=self.config.startup_timeout_seconds,
            )
        except ServerExitedError as exc:
            bind_failure = (
                "bind failure" in exc.log_tail.lower()
                or "address already in use" in exc.log_tail.lower()
            )
            port = self.config.port
            self.stop()
            if bind_failure and port is not None:
                raise PortInUseError(self.config.host, port) from exc
            raise
        except Exception:
            self.stop()
            raise

    def kleidiai_evidence(self) -> KleidiAIEvidence:
        return analyze_kleidiai_evidence(self.log_text)

    def stop(self) -> ShutdownResult:
        if self._shutdown_result is not None:
            return self._shutdown_result
        started = time.monotonic()
        process = self._process
        if self._client is not None:
            self._client.close()
            self._client = None
        if process is None:
            result = ShutdownResult(
                stopped=True,
                was_running=False,
                graceful=True,
                forced=False,
                return_code=None,
                elapsed_seconds=time.monotonic() - started,
            )
            self._shutdown_result = result
            return result

        was_running = process.poll() is None
        forced = False
        graceful = True
        if was_running:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=self.original_config.shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                graceful = False
                forced = True
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired as exc:
                    raise ProcessShutdownError(
                        f"Owned llama-server process group {process.pid} survived SIGKILL"
                    ) from exc
        else:
            process.wait()
        for thread in self._threads:
            thread.join(timeout=1.0)
        result = ShutdownResult(
            stopped=process.poll() is not None,
            was_running=was_running,
            graceful=graceful,
            forced=forced,
            return_code=process.poll(),
            elapsed_seconds=time.monotonic() - started,
        )
        self._shutdown_result = result
        return result

    def __enter__(self) -> LlamaServerProcess:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()


def write_startup_log(path: Path, process: LlamaServerProcess) -> None:
    """Write only the redacted bounded log retained by the owner."""

    path.write_text(process.log_text, encoding="utf-8")
