from __future__ import annotations

import os
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from aarchtune.runtime import capabilities
from aarchtune.runtime.capabilities import (
    clear_capability_cache,
    inspect_llama_server_capabilities,
    parse_supported_option_tokens,
    resolve_llama_server_binary,
)
from aarchtune.runtime.errors import BinaryNotExecutableError, BinaryNotFoundError


def test_explicit_binary_path_and_version_success(fake_binary: Path) -> None:
    result = inspect_llama_server_capabilities(fake_binary, use_cache=False)

    assert result.binary_path == fake_binary
    assert result.version == "fake-llama-server 1.0 (synthetic)"
    assert result.version_probe.successful is True
    assert result.help_probe.successful is True


def test_probe_output_is_preserved_only_when_requested(fake_binary: Path) -> None:
    clear_capability_cache()
    quiet = inspect_llama_server_capabilities(fake_binary, include_probe_output=False)
    diagnostic = inspect_llama_server_capabilities(fake_binary, include_probe_output=True)

    assert quiet.help_probe.stdout is None
    assert diagnostic.help_probe.stdout is not None
    assert "--model" in diagnostic.help_probe.stdout


def test_path_discovery(fake_binary: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(capabilities.shutil, "which", lambda name: str(fake_binary))

    assert resolve_llama_server_binary() == fake_binary


def test_missing_binary() -> None:
    with pytest.raises(BinaryNotFoundError, match="not found"):
        resolve_llama_server_binary(Path("/definitely/missing/llama-server"))


def test_non_executable_binary(tmp_path: Path) -> None:
    binary = tmp_path / "llama-server"
    binary.write_text("fixture", encoding="utf-8")
    binary.chmod(0o644)

    with pytest.raises(BinaryNotExecutableError, match="not executable"):
        resolve_llama_server_binary(binary)


def test_version_timeout_is_structured(fake_binary: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", "version-timeout")
    monkeypatch.setenv("FAKE_LLAMA_DELAY", "0.3")

    result = inspect_llama_server_capabilities(
        fake_binary,
        timeout_seconds=0.05,
        include_probe_output=True,
        use_cache=False,
    )

    assert result.version is None
    assert result.version_probe.timed_out is True
    assert "timed out" in (result.version_probe.error or "")


def test_help_failure_does_not_infer_flags_from_version(
    fake_binary: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", "help-failure")

    result = inspect_llama_server_capabilities(fake_binary, use_cache=False)

    assert result.version is not None
    assert result.help_probe.successful is False
    assert result.supported_flags == set()


def test_complete_flag_token_parsing_prevents_substring_false_positives() -> None:
    raw, canonical = parse_supported_option_tokens(
        "--model FILE --model-cache DIR text--port --port=8080 --threads-batch N"
    )

    assert "--model" in raw
    assert "--model-cache" in raw
    assert "--port" in raw
    assert "--threads-batch" in raw
    assert "--threads" not in raw
    assert canonical >= {"--model", "--model-cache", "--port", "--threads-batch"}


def test_alias_tokens_are_canonicalized() -> None:
    raw, canonical = parse_supported_option_tokens(
        "--context-size N --threads_batch N --batch_size N --ubatch_size N"
    )

    assert "--context-size" in raw
    assert canonical == {"--ctx-size", "--threads-batch", "--batch-size", "--ubatch-size"}


def test_cache_reuses_probes(fake_binary: Path, monkeypatch: MonkeyPatch) -> None:
    clear_capability_cache()
    original = capabilities._run_probe
    calls = 0

    def counting_probe(binary: Path, argument: str, timeout_seconds: float) -> object:
        nonlocal calls
        calls += 1
        return original(binary, argument, timeout_seconds)

    monkeypatch.setattr(capabilities, "_run_probe", counting_probe)
    inspect_llama_server_capabilities(fake_binary)
    inspect_llama_server_capabilities(fake_binary)

    assert calls == 2


def test_cache_invalidates_when_binary_metadata_changes(
    fake_binary: Path, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    copied = tmp_path / "fake-server"
    copied.write_bytes(fake_binary.read_bytes())
    copied.chmod(0o755)
    clear_capability_cache()
    original = capabilities._run_probe
    calls = 0

    def counting_probe(binary: Path, argument: str, timeout_seconds: float) -> object:
        nonlocal calls
        calls += 1
        return original(binary, argument, timeout_seconds)

    monkeypatch.setattr(capabilities, "_run_probe", counting_probe)
    inspect_llama_server_capabilities(copied)
    copied.write_bytes(copied.read_bytes() + b"\n")
    os.utime(copied, None)
    inspect_llama_server_capabilities(copied)

    assert calls == 4
