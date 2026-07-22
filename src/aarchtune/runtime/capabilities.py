"""Version-independent llama-server option probing and evidence analysis."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from aarchtune.models import KleidiAIStatus
from aarchtune.runtime.errors import BinaryNotExecutableError, BinaryNotFoundError
from aarchtune.runtime.redaction import redact_text

PROBED_FLAGS = frozenset(
    {
        "--model",
        "--host",
        "--port",
        "--threads",
        "--threads-batch",
        "--batch-size",
        "--ubatch-size",
        "--ctx-size",
        "--parallel",
        "--metrics",
        "--seed",
        "--temp",
        "--no-mmap",
        "--mmap",
        "--numa",
        "--cache-prompt",
        "--log-file",
    }
)

_OPTION_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])(--[A-Za-z0-9][A-Za-z0-9_-]*)(?=$|[\s=,\[<])")
_FLAG_ALIASES = {
    "--context-size": "--ctx-size",
    "--n-ctx": "--ctx-size",
    "--threads_batch": "--threads-batch",
    "--batch_size": "--batch-size",
    "--ubatch_size": "--ubatch-size",
}
_POSITIVE_KLEIDIAI = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bkleidi\s*ai\b[^\n]*(?:enabled|active|backend|buffer)",
        r"(?:using|loaded|backend:)\s+\bkleidi\s*ai\b",
        r"\bkleidi\s*ai\b.*\b(?:on|yes)\b",
    )
)
_NEGATIVE_KLEIDIAI = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bkleidi\s*ai\b[^\n]*(?:disabled|unavailable|not compiled|not enabled)",
        r"\bwithout\s+kleidi\s*ai\b",
        r"\bkleidi\s*ai\b\s*[:=]\s*(?:off|no|false)\b",
    )
)
_MAX_PROBE_OUTPUT = 128 * 1024


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProbeResult(RuntimeModel):
    arguments: list[str]
    exit_code: int | None
    timed_out: bool
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None

    @property
    def successful(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and self.error is None


class KleidiAIEvidence(RuntimeModel):
    status: KleidiAIStatus
    evidence: list[str] = Field(default_factory=list)


class ServerCapabilities(RuntimeModel):
    binary_path: Path
    binary_size: int
    binary_mtime_ns: int
    version: str | None = None
    raw_option_tokens: set[str] = Field(default_factory=set)
    supported_flags: set[str] = Field(default_factory=set)
    version_probe: ProbeResult
    help_probe: ProbeResult
    kleidiai_status: KleidiAIStatus = KleidiAIStatus.UNKNOWN
    kleidiai_evidence: list[str] = Field(default_factory=list)

    def supports(self, flag: str) -> bool:
        return flag in self.supported_flags


_CAPABILITY_CACHE: dict[tuple[str, int, int], ServerCapabilities] = {}
_CACHE_LOCK = threading.Lock()


def clear_capability_cache() -> None:
    with _CACHE_LOCK:
        _CAPABILITY_CACHE.clear()


def resolve_llama_server_binary(binary_path: Path | None = None) -> Path:
    """Resolve an explicit path or PATH entry and enforce executable ownership preconditions."""

    candidate: Path | None
    if binary_path is None:
        discovered = shutil.which("llama-server")
        candidate = Path(discovered) if discovered else None
    else:
        candidate = binary_path.expanduser()
    if candidate is None or not candidate.is_file():
        label = str(candidate) if candidate is not None else "llama-server on PATH"
        raise BinaryNotFoundError(f"llama-server binary not found: {label}")
    if not os.access(candidate, os.X_OK):
        raise BinaryNotExecutableError(f"llama-server binary is not executable: {candidate}")
    return candidate.resolve()


def _run_probe(binary: Path, argument: str, timeout_seconds: float) -> ProbeResult:
    arguments = [str(binary), argument]
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        )
        stderr = (
            exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        )
        return ProbeResult(
            arguments=arguments,
            exit_code=None,
            timed_out=True,
            stdout=(stdout or "")[:_MAX_PROBE_OUTPUT],
            stderr=(stderr or "")[:_MAX_PROBE_OUTPUT],
            error=f"Probe timed out after {timeout_seconds:.2f} seconds",
        )
    except OSError as exc:
        return ProbeResult(
            arguments=arguments,
            exit_code=None,
            timed_out=False,
            error=f"Probe failed: {exc}",
        )
    return ProbeResult(
        arguments=arguments,
        exit_code=completed.returncode,
        timed_out=False,
        stdout=completed.stdout[:_MAX_PROBE_OUTPUT],
        stderr=completed.stderr[:_MAX_PROBE_OUTPUT],
        error=(
            None
            if completed.returncode == 0
            else f"Probe exited with status {completed.returncode}"
        ),
    )


def parse_supported_option_tokens(help_output: str) -> tuple[set[str], set[str]]:
    """Extract complete long-option tokens and separately return canonical known aliases."""

    raw_tokens = set(_OPTION_TOKEN.findall(help_output))
    canonical = {_FLAG_ALIASES.get(token, token) for token in raw_tokens}
    return raw_tokens, canonical


def analyze_kleidiai_evidence(text: str) -> KleidiAIEvidence:
    """Classify only recognized positive or affirmative negative log evidence."""

    positive: list[str] = []
    negative: list[str] = []
    for raw_line in text.splitlines():
        compact = " ".join(raw_line.strip().split())
        if not compact:
            continue
        safe_line = redact_text(compact)[:500]
        if any(pattern.search(compact) for pattern in _POSITIVE_KLEIDIAI):
            if safe_line not in positive:
                positive.append(safe_line)
        elif (
            any(pattern.search(compact) for pattern in _NEGATIVE_KLEIDIAI)
            and safe_line not in negative
        ):
            negative.append(safe_line)
    if positive:
        return KleidiAIEvidence(status=KleidiAIStatus.VERIFIED, evidence=positive)
    if negative:
        return KleidiAIEvidence(status=KleidiAIStatus.NOT_DETECTED, evidence=negative)
    return KleidiAIEvidence(status=KleidiAIStatus.UNKNOWN)


def _without_probe_output(capabilities: ServerCapabilities) -> ServerCapabilities:
    def stripped(probe: ProbeResult) -> ProbeResult:
        return probe.model_copy(update={"stdout": None, "stderr": None})

    return capabilities.model_copy(
        update={
            "version_probe": stripped(capabilities.version_probe),
            "help_probe": stripped(capabilities.help_probe),
        }
    )


def inspect_llama_server_capabilities(
    binary_path: Path | None = None,
    *,
    timeout_seconds: float = 5.0,
    include_probe_output: bool = False,
    use_cache: bool = True,
) -> ServerCapabilities:
    """Probe help/version directly; version strings never imply flag support."""

    binary = resolve_llama_server_binary(binary_path)
    metadata = binary.stat()
    cache_key = (str(binary), metadata.st_size, metadata.st_mtime_ns)
    with _CACHE_LOCK:
        cached = _CAPABILITY_CACHE.get(cache_key) if use_cache else None
    if cached is not None:
        return cached if include_probe_output else _without_probe_output(cached)

    version_probe = _run_probe(binary, "--version", timeout_seconds)
    help_probe = _run_probe(binary, "--help", timeout_seconds)
    help_text = f"{help_probe.stdout or ''}\n{help_probe.stderr or ''}"
    raw_tokens, canonical_tokens = parse_supported_option_tokens(help_text)
    version_text = f"{version_probe.stdout or ''}\n{version_probe.stderr or ''}"
    evidence = analyze_kleidiai_evidence(f"{version_text}\n{help_text}")
    version = None
    if version_probe.successful:
        version = next(
            (line.strip() for line in version_text.splitlines() if line.strip()),
            None,
        )
    capabilities = ServerCapabilities(
        binary_path=binary,
        binary_size=metadata.st_size,
        binary_mtime_ns=metadata.st_mtime_ns,
        version=version,
        raw_option_tokens=raw_tokens,
        supported_flags=canonical_tokens,
        version_probe=version_probe,
        help_probe=help_probe,
        kleidiai_status=evidence.status,
        kleidiai_evidence=evidence.evidence,
    )
    with _CACHE_LOCK:
        for old_key in [key for key in _CAPABILITY_CACHE if key[0] == str(binary)]:
            _CAPABILITY_CACHE.pop(old_key, None)
        _CAPABILITY_CACHE[cache_key] = capabilities
    return capabilities if include_probe_output else _without_probe_output(capabilities)
