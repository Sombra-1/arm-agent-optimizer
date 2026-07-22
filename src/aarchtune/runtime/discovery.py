"""Discover local llama.cpp binaries without assuming a build layout."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from aarchtune.models import BinaryInspection, LlamaCppInspection
from aarchtune.runtime.capabilities import analyze_kleidiai_evidence

_MAX_CAPTURE_CHARS = 64_000


@dataclass(frozen=True)
class _VersionProbe:
    version: str | None
    output: str
    error: str | None
    completed: bool


def _candidate_path(name: str, environment_variable: str) -> Path | None:
    override = os.environ.get(environment_variable)
    if override:
        return Path(override).expanduser()
    discovered = shutil.which(name)
    return Path(discovered) if discovered else None


def _probe_version(path: Path, *, timeout_seconds: float = 5.0) -> _VersionProbe:
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return _VersionProbe(None, "", "Version probe timed out", False)
    except OSError as exc:
        return _VersionProbe(None, "", f"Version probe failed: {exc}", False)

    output = f"{result.stdout}\n{result.stderr}"[:_MAX_CAPTURE_CHARS].strip()
    first_line = next((line.strip() for line in output.splitlines() if line.strip()), None)
    error = None
    if result.returncode != 0:
        error = f"Version command exited with status {result.returncode}"
    return _VersionProbe(first_line, output, error, True)


def inspect_binary(name: str, environment_variable: str) -> tuple[BinaryInspection, str, bool]:
    """Inspect one executable and return its model, captured evidence, and probe state."""

    path = _candidate_path(name, environment_variable)
    if path is None:
        return BinaryInspection(name=name, found=False, error="Not found on PATH"), "", False
    if not path.is_file():
        return (
            BinaryInspection(
                name=name,
                found=False,
                error=f"Configured path is not a file: {path}",
            ),
            "",
            False,
        )
    if not os.access(path, os.X_OK):
        return (
            BinaryInspection(name=name, path=path, found=False, error="File is not executable"),
            "",
            False,
        )

    resolved = path.resolve()
    probe = _probe_version(resolved)
    return (
        BinaryInspection(
            name=name,
            path=resolved,
            found=True,
            version=probe.version,
            error=probe.error,
        ),
        probe.output,
        probe.completed,
    )


def discover_llama_cpp() -> LlamaCppInspection:
    """Discover llama-server/llama-bench and classify direct KleidiAI evidence."""

    server, server_output, server_probed = inspect_binary("llama-server", "AARCHTUNE_LLAMA_SERVER")
    bench, bench_output, bench_probed = inspect_binary("llama-bench", "AARCHTUNE_LLAMA_BENCH")
    del server_probed, bench_probed
    kleidiai = analyze_kleidiai_evidence(f"{server_output}\n{bench_output}")

    return LlamaCppInspection(
        server_path=server.path if server.found else None,
        bench_path=bench.path if bench.found else None,
        version=server.version or bench.version,
        server=server,
        bench=bench,
        kleidiai_status=kleidiai.status,
        kleidiai_evidence=kleidiai.evidence,
    )
