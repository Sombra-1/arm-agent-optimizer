"""Expected failures from local llama-server runtime management."""

from __future__ import annotations

from pathlib import Path

from aarchtune.errors import AArchTuneError


class RuntimeManagementError(AArchTuneError):
    """Base class for safe runtime-management failures."""


class BinaryNotFoundError(RuntimeManagementError):
    """The requested llama-server executable is absent."""


class BinaryNotExecutableError(RuntimeManagementError):
    """The requested llama-server path is not executable."""


class CapabilityInspectionError(RuntimeManagementError):
    """The binary could not be inspected reliably."""


class ConfigurationError(RuntimeManagementError):
    """A server configuration is unsafe or invalid."""


class UnsupportedConfigurationError(ConfigurationError):
    """A requested setting has no proven supported CLI mapping."""

    def __init__(self, setting: str, required_flags: tuple[str, ...]) -> None:
        self.setting = setting
        self.required_flags = required_flags
        joined = " or ".join(required_flags)
        super().__init__(f"Setting {setting!r} requires unsupported flag {joined}")


class PortInUseError(RuntimeManagementError):
    """An explicit server port is already occupied."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        super().__init__(
            f"Cannot start llama-server: {host}:{port} is already in use. "
            "Choose another port or omit it for automatic loopback selection."
        )


class ProcessStartError(RuntimeManagementError):
    """Popen failed before a child process was owned."""


class ServerExitedError(RuntimeManagementError):
    """The owned process exited before readiness."""

    def __init__(self, return_code: int, log_tail: str) -> None:
        self.return_code = return_code
        self.log_tail = log_tail
        suffix = f" Recent logs:\n{log_tail}" if log_tail else ""
        super().__init__(f"llama-server exited before readiness with status {return_code}.{suffix}")


class ReadinessTimeoutError(RuntimeManagementError):
    """No configured network endpoint became ready before the deadline."""

    def __init__(self, timeout_seconds: float, last_error: str | None, log_tail: str) -> None:
        self.timeout_seconds = timeout_seconds
        self.last_error = last_error
        self.log_tail = log_tail
        details = f" Last probe: {last_error}." if last_error else ""
        logs = f" Recent logs:\n{log_tail}" if log_tail else ""
        super().__init__(
            f"llama-server was not ready within {timeout_seconds:.2f} seconds.{details}{logs}"
        )


class ProcessShutdownError(RuntimeManagementError):
    """The owned process could not be stopped after escalation."""


class ArtifactWriteError(RuntimeManagementError):
    """A requested runtime diagnostic artifact could not be written."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        super().__init__(f"Could not write runtime artifact {path}: {reason}")
