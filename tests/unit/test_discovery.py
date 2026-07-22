from __future__ import annotations

import subprocess
from pathlib import Path

from pytest import MonkeyPatch

from aarchtune.models import KleidiAIStatus
from aarchtune.runtime import discovery


def _executable(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_missing_binaries_have_unknown_kleidiai_status(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("AARCHTUNE_LLAMA_SERVER", raising=False)
    monkeypatch.delenv("AARCHTUNE_LLAMA_BENCH", raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda _name: None)

    result = discovery.discover_llama_cpp()

    assert result.server.found is False
    assert result.bench.found is False
    assert result.kleidiai_status is KleidiAIStatus.UNKNOWN


def test_direct_kleidiai_version_evidence_is_verified(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    server = _executable(tmp_path, "llama-server")
    monkeypatch.setenv("AARCHTUNE_LLAMA_SERVER", str(server))
    monkeypatch.delenv("AARCHTUNE_LLAMA_BENCH", raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda _name: None)

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[str(server), "--version"],
            returncode=0,
            stdout="llama.cpp b1234\nbackend: KleidiAI enabled\n",
            stderr="",
        )

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)

    result = discovery.discover_llama_cpp()

    assert result.server_path == server.resolve()
    assert result.version == "llama.cpp b1234"
    assert result.kleidiai_status is KleidiAIStatus.VERIFIED
    assert result.kleidiai_evidence == ["backend: KleidiAI enabled"]


def test_successful_probe_without_evidence_remains_unknown(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    bench = _executable(tmp_path, "llama-bench")
    monkeypatch.setenv("AARCHTUNE_LLAMA_BENCH", str(bench))
    monkeypatch.delenv("AARCHTUNE_LLAMA_SERVER", raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        discovery.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[str(bench), "--version"], returncode=0, stdout="version 99\n", stderr=""
        ),
    )

    result = discovery.discover_llama_cpp()

    assert result.kleidiai_status is KleidiAIStatus.UNKNOWN
    assert result.kleidiai_evidence == []


def test_non_executable_override_is_not_found(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "llama-server"
    path.write_text("not executable", encoding="utf-8")
    monkeypatch.setenv("AARCHTUNE_LLAMA_SERVER", str(path))

    result, output, probed = discovery.inspect_binary("llama-server", "AARCHTUNE_LLAMA_SERVER")

    assert result.found is False
    assert result.error == "File is not executable"
    assert output == ""
    assert probed is False
