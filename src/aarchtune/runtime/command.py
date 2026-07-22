"""Pure, explainable llama-server argument-list construction."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from aarchtune.runtime.capabilities import ServerCapabilities
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.errors import ConfigurationError, UnsupportedConfigurationError
from aarchtune.runtime.redaction import redact_environment

_ACTUAL_ALIASES: dict[str, tuple[str, ...]] = {
    "--ctx-size": ("--ctx-size", "--context-size", "--n-ctx"),
    "--threads-batch": ("--threads-batch", "--threads_batch"),
    "--batch-size": ("--batch-size", "--batch_size"),
    "--ubatch-size": ("--ubatch-size", "--ubatch_size"),
}


class CommandBuildResult(BaseModel):
    """Arguments plus an auditable explanation of every mapping decision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    arguments: list[str]
    requested_settings: dict[str, JsonValue]
    mapped_flags: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def binary_path(self) -> Path:
        return Path(self.arguments[0])


def _actual_supported_flag(capabilities: ServerCapabilities, canonical: str) -> str | None:
    candidates = _ACTUAL_ALIASES.get(canonical, (canonical,))
    return next((flag for flag in candidates if flag in capabilities.raw_option_tokens), None)


def _require_flag(
    capabilities: ServerCapabilities,
    setting: str,
    canonical: str,
) -> str:
    actual = _actual_supported_flag(capabilities, canonical)
    if actual is None:
        raise UnsupportedConfigurationError(setting, _ACTUAL_ALIASES.get(canonical, (canonical,)))
    return actual


def build_llama_server_command(
    config: LlamaServerConfig,
    capabilities: ServerCapabilities,
) -> CommandBuildResult:
    """Map typed settings to proven help tokens; never create a shell command."""

    if config.binary_path != capabilities.binary_path:
        raise ConfigurationError(
            "configuration binary_path differs from the inspected capability binary"
        )
    if config.port is None:
        raise ConfigurationError("automatic port must be resolved before command construction")

    arguments = [str(config.binary_path)]
    mapped: dict[str, str] = {}
    warnings: list[str] = []

    def add_value(setting: str, canonical: str, value: object) -> None:
        flag = _require_flag(capabilities, setting, canonical)
        arguments.extend([flag, str(value)])
        mapped[setting] = flag

    def add_switch(setting: str, canonical: str) -> None:
        flag = _require_flag(capabilities, setting, canonical)
        arguments.append(flag)
        mapped[setting] = flag

    add_value("model_path", "--model", config.model_path)
    add_value("host", "--host", config.host)
    add_value("port", "--port", config.port)

    optional_values = (
        ("threads", "--threads", config.threads),
        ("threads_batch", "--threads-batch", config.threads_batch),
        ("batch_size", "--batch-size", config.batch_size),
        ("ubatch_size", "--ubatch-size", config.ubatch_size),
        ("context_size", "--ctx-size", config.context_size),
        ("parallel_slots", "--parallel", config.parallel_slots),
    )
    for setting, flag, value in optional_values:
        if value is not None:
            add_value(setting, flag, value)

    if config.metrics_enabled:
        add_switch("metrics_enabled", "--metrics")
    if config.prompt_cache:
        add_switch("prompt_cache", "--cache-prompt")
    if config.mmap:
        mmap_flag = _actual_supported_flag(capabilities, "--mmap")
        if mmap_flag is not None:
            arguments.append(mmap_flag)
            mapped["mmap"] = mmap_flag
        elif _actual_supported_flag(capabilities, "--no-mmap") is not None:
            warnings.append(
                "mmap=true uses the runtime default proven by availability of --no-mmap"
            )
        else:
            raise UnsupportedConfigurationError("mmap", ("--mmap", "--no-mmap"))
    else:
        add_switch("mmap", "--no-mmap")
    if config.numa_mode != "disabled":
        add_value("numa_mode", "--numa", config.numa_mode)

    requested: dict[str, JsonValue] = {
        "binary_path": str(config.binary_path),
        "model_path": str(config.model_path),
        "host": config.host,
        "port": config.port,
        "threads": config.threads,
        "threads_batch": config.threads_batch,
        "batch_size": config.batch_size,
        "ubatch_size": config.ubatch_size,
        "context_size": config.context_size,
        "parallel_slots": config.parallel_slots,
        "metrics_enabled": config.metrics_enabled,
        "prompt_cache": config.prompt_cache,
        "mmap": config.mmap,
        "numa_mode": config.numa_mode,
        "extra_environment": cast(JsonValue, redact_environment(config.extra_environment)),
    }
    return CommandBuildResult(
        arguments=arguments,
        requested_settings=requested,
        mapped_flags=mapped,
        warnings=warnings,
    )
