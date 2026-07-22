"""Strict typed configuration for one local llama-server process."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LlamaServerConfig(BaseModel):
    """Validated server settings; raw CLI fragments are intentionally impossible."""

    model_config = ConfigDict(extra="forbid", strict=True)

    binary_path: Path
    model_path: Path
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65_535)] | None = None
    allow_public_bind: bool = False
    threads: Annotated[int, Field(ge=1, le=4_096)] | None = None
    threads_batch: Annotated[int, Field(ge=1, le=4_096)] | None = None
    batch_size: Annotated[int, Field(ge=1, le=1_048_576)] | None = None
    ubatch_size: Annotated[int, Field(ge=1, le=1_048_576)] | None = None
    context_size: Annotated[int, Field(ge=1, le=1_048_576)] | None = None
    parallel_slots: Annotated[int, Field(ge=1, le=1_024)] | None = None
    metrics_enabled: bool = False
    prompt_cache: bool = False
    mmap: bool = True
    numa_mode: Literal["disabled", "distribute", "isolate", "numactl"] = "disabled"
    startup_timeout_seconds: Annotated[float, Field(ge=0.1, le=600.0)] = 30.0
    request_timeout_seconds: Annotated[float, Field(ge=0.1, le=600.0)] = 60.0
    shutdown_timeout_seconds: Annotated[float, Field(ge=0.1, le=60.0)] = 5.0
    maximum_log_bytes: Annotated[int, Field(ge=256, le=10 * 1024 * 1024)] = 1024 * 1024
    extra_environment: dict[str, str] = Field(default_factory=dict)
    readiness_endpoints: tuple[str, ...] = ("/health", "/v1/models")

    @field_validator("binary_path")
    @classmethod
    def validate_binary(cls, path: Path) -> Path:
        expanded = path.expanduser()
        if not expanded.is_file():
            raise ValueError("binary_path must be an existing regular file")
        if not os.access(expanded, os.X_OK):
            raise ValueError("binary_path must be executable")
        return expanded.resolve()

    @field_validator("model_path")
    @classmethod
    def validate_model(cls, path: Path) -> Path:
        expanded = path.expanduser()
        if not expanded.is_file():
            raise ValueError("model_path must be an existing regular file")
        if not os.access(expanded, os.R_OK):
            raise ValueError("model_path must be readable")
        return expanded.resolve()

    @field_validator("host")
    @classmethod
    def validate_host(cls, host: str) -> str:
        if not host or any(character.isspace() for character in host) or "\x00" in host:
            raise ValueError("host must be a non-empty address without whitespace")
        return host

    @field_validator("extra_environment")
    @classmethod
    def validate_environment(cls, environment: dict[str, str]) -> dict[str, str]:
        for name, value in environment.items():
            if not _ENVIRONMENT_NAME.fullmatch(name):
                raise ValueError(f"invalid environment variable name: {name!r}")
            if "\x00" in value:
                raise ValueError(f"environment value for {name!r} contains a null byte")
            if len(value) > 32_768:
                raise ValueError(f"environment value for {name!r} is too long")
        return environment

    @field_validator("readiness_endpoints")
    @classmethod
    def validate_readiness_endpoints(cls, endpoints: tuple[str, ...]) -> tuple[str, ...]:
        if not endpoints:
            raise ValueError("at least one readiness endpoint is required")
        for endpoint in endpoints:
            if not endpoint.startswith("/") or endpoint.startswith("//"):
                raise ValueError("readiness endpoints must be absolute local paths")
            if any(character.isspace() for character in endpoint) or "?" in endpoint:
                raise ValueError("readiness endpoints cannot contain whitespace or queries")
        return endpoints

    @model_validator(mode="after")
    def reject_public_bind_without_opt_in(self) -> LlamaServerConfig:
        if self.host not in _LOOPBACK_HOSTS and not self.host.startswith("127."):
            if not self.allow_public_bind:
                raise ValueError(
                    "public or non-loopback host requires allow_public_bind=true explicitly"
                )
            if self.port is None:
                raise ValueError("automatic port selection is only supported on loopback hosts")
        if (
            self.ubatch_size is not None
            and self.batch_size is not None
            and self.ubatch_size > self.batch_size
        ):
            raise ValueError("ubatch_size cannot exceed batch_size")
        return self
