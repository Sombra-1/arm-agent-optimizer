from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from aarchtune.runtime.capabilities import ServerCapabilities
from aarchtune.runtime.command import build_llama_server_command
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.errors import ConfigurationError, UnsupportedConfigurationError
from aarchtune.runtime.redaction import redact_environment, redact_text


def test_minimal_valid_config(
    config_factory: Callable[..., LlamaServerConfig],
) -> None:
    config = config_factory()

    assert config.host == "127.0.0.1"
    assert config.port is None
    assert config.mmap is True


def test_invalid_binary_and_model(fake_binary: Path, fake_model: Path, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="binary_path"):
        LlamaServerConfig(binary_path=tmp_path / "missing", model_path=fake_model)
    with pytest.raises(ValidationError, match="model_path"):
        LlamaServerConfig(binary_path=fake_binary, model_path=tmp_path / "missing.gguf")


def test_public_host_requires_explicit_opt_in(fake_binary: Path, fake_model: Path) -> None:
    with pytest.raises(ValidationError, match="allow_public_bind"):
        LlamaServerConfig(
            binary_path=fake_binary,
            model_path=fake_model,
            host="0.0.0.0",
            port=8080,
        )
    config = LlamaServerConfig(
        binary_path=fake_binary,
        model_path=fake_model,
        host="0.0.0.0",
        port=8080,
        allow_public_bind=True,
    )
    assert config.host == "0.0.0.0"


@pytest.mark.parametrize(
    ("field", "value"),
    [("port", 0), ("threads", 0), ("batch_size", -1), ("startup_timeout_seconds", 0)],
)
def test_invalid_numeric_settings(
    fake_binary: Path, fake_model: Path, field: str, value: object
) -> None:
    values = {"binary_path": fake_binary, "model_path": fake_model, field: value}
    with pytest.raises(ValidationError):
        LlamaServerConfig.model_validate(values)


def test_environment_validation(fake_binary: Path, fake_model: Path) -> None:
    with pytest.raises(ValidationError, match="invalid environment"):
        LlamaServerConfig(
            binary_path=fake_binary,
            model_path=fake_model,
            extra_environment={"BAD-NAME": "value"},
        )
    with pytest.raises(ValidationError, match="null byte"):
        LlamaServerConfig(
            binary_path=fake_binary,
            model_path=fake_model,
            extra_environment={"GOOD_NAME": "bad\x00value"},
        )


def test_secret_redaction() -> None:
    environment = redact_environment({"API_TOKEN": "secret-value", "MODE": "safe"})

    assert environment == {"API_TOKEN": "<redacted>", "MODE": "safe"}
    assert "abc123" not in redact_text("authorization=abc123")


def test_unsupported_requested_flag(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    config = config_factory(port=12345, threads=8)
    reduced = server_capabilities.model_copy(
        update={
            "raw_option_tokens": server_capabilities.raw_option_tokens - {"--threads"},
            "supported_flags": server_capabilities.supported_flags - {"--threads"},
        }
    )

    with pytest.raises(UnsupportedConfigurationError) as raised:
        build_llama_server_command(config, reduced)

    assert raised.value.setting == "threads"


def test_auto_port_must_be_resolved_before_build(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
) -> None:
    with pytest.raises(ConfigurationError, match="automatic port"):
        build_llama_server_command(config_factory(), server_capabilities)


def test_paths_with_spaces_and_exact_argument_generation(
    fake_binary: Path,
    fake_model: Path,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "paths with spaces"
    directory.mkdir()
    binary = directory / "fake llama server"
    model = directory / "fake model.gguf"
    binary.write_bytes(fake_binary.read_bytes())
    binary.chmod(0o755)
    model.write_bytes(fake_model.read_bytes())
    from aarchtune.runtime.capabilities import inspect_llama_server_capabilities

    capabilities = inspect_llama_server_capabilities(binary, use_cache=False)
    config = LlamaServerConfig(
        binary_path=binary,
        model_path=model,
        port=38291,
        threads=8,
        threads_batch=9,
        batch_size=512,
        ubatch_size=128,
        context_size=4096,
        parallel_slots=2,
        metrics_enabled=True,
        prompt_cache=True,
        mmap=False,
        numa_mode="distribute",
        extra_environment={"API_TOKEN": "secret-value"},
    )

    result = build_llama_server_command(config, capabilities)

    assert result.arguments == [
        str(binary.resolve()),
        "--model",
        str(model.resolve()),
        "--host",
        "127.0.0.1",
        "--port",
        "38291",
        "--threads",
        "8",
        "--threads-batch",
        "9",
        "--batch-size",
        "512",
        "--ubatch-size",
        "128",
        "--ctx-size",
        "4096",
        "--parallel",
        "2",
        "--metrics",
        "--cache-prompt",
        "--no-mmap",
        "--numa",
        "distribute",
    ]
    assert result.requested_settings["extra_environment"] == {"API_TOKEN": "<redacted>"}
    assert "secret-value" not in str(result.model_dump())
