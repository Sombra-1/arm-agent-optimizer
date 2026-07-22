"""Version-aware llama-bench discovery and option-token inspection."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.optimization.models import SearchPlan
from aarchtune.runtime.capabilities import ProbeResult
from aarchtune.screening.errors import BenchCapabilityError, BenchDiscoveryError
from aarchtune.screening.models import (
    CapabilityMapping,
    LlamaBenchCapabilities,
    OutputFormat,
    OutputFormatSelection,
)

BENCH_ENVIRONMENT_VARIABLE = "AARCHTUNE_LLAMA_BENCH"
_MAX_PROBE_OUTPUT = 128 * 1024
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])(--[A-Za-z0-9][A-Za-z0-9_-]*|-[A-Za-z][A-Za-z0-9]*)(?=$|[\s=,\[<])"
)
_ALIASES: dict[str, tuple[str, ...]] = {
    "model_path": ("-m", "--model"),
    "threads": ("-t", "--threads"),
    "threads_batch": ("-tb", "--threads-batch", "--threads_batch"),
    "batch_size": ("-b", "--batch-size", "--batch_size"),
    "ubatch_size": ("-ub", "--ubatch-size", "--ubatch_size"),
    "prompt_tokens": ("-p", "--prompt-tokens", "--n-prompt"),
    "generation_tokens": ("-n", "--generation-tokens", "--n-gen"),
    "repetitions": ("-r", "--repetitions"),
    "output_format": ("-o", "--output", "--output-format", "--jsonl", "--json", "--csv"),
    "numa_mode": ("--numa",),
    "mmap": ("--mmap", "--no-mmap"),
    "cpu_mask": ("--cpu-mask", "--cpu-range"),
    "verbosity": ("-v", "--verbose"),
}

_CACHE: dict[tuple[str, int, int], LlamaBenchCapabilities] = {}
_LOCK = threading.Lock()


def clear_bench_capability_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _valid_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_llama_bench_binary(
    explicit: Path | None = None, *, plan: SearchPlan | None = None
) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser()
        if not candidate.is_file():
            raise BenchDiscoveryError(f"llama-bench executable not found: {candidate}")
        if not os.access(candidate, os.X_OK):
            raise BenchDiscoveryError(f"llama-bench path is not executable: {candidate}")
        return candidate.resolve()
    override = os.environ.get(BENCH_ENVIRONMENT_VARIABLE)
    if override:
        return resolve_llama_bench_binary(Path(override))
    for name in ("llama-bench", "benchmark"):
        discovered = shutil.which(name)
        if discovered and (name == "llama-bench" or Path(discovered).name == "benchmark"):
            return Path(discovered).resolve()
    if plan is not None:
        sibling_directory = plan.input.runtime.binary_path.parent
        for name in ("llama-bench", "benchmark"):
            sibling = sibling_directory / name
            if _valid_executable(sibling):
                return sibling.resolve()
    known = [
        Path.cwd() / "build/bin/llama-bench",
        Path("/opt/llama.cpp/build/bin/llama-bench"),
    ]
    matches = [path.resolve() for path in known if _valid_executable(path)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise BenchDiscoveryError(
            "Multiple local llama-bench executables found; pass --bench-binary explicitly"
        )
    raise BenchDiscoveryError(
        "llama-bench not found; pass --bench-binary or set AARCHTUNE_LLAMA_BENCH"
    )


def _probe(binary: Path, argument: str, timeout_seconds: float) -> ProbeResult:
    arguments = [str(binary), argument]
    try:
        result = subprocess.run(
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
        exit_code=result.returncode,
        timed_out=False,
        stdout=result.stdout[:_MAX_PROBE_OUTPUT],
        stderr=result.stderr[:_MAX_PROBE_OUTPUT],
        error=None if result.returncode == 0 else f"Probe exited with status {result.returncode}",
    )


def parse_option_tokens(help_text: str) -> set[str]:
    return set(_TOKEN.findall(help_text))


def _mapping(name: str, tokens: set[str]) -> CapabilityMapping:
    observed = [alias for alias in _ALIASES[name] if alias in tokens]
    return CapabilityMapping(
        logical_parameter=name,
        supported=bool(observed),
        selected_flag=observed[0] if observed else None,
        aliases_observed=observed,
    )


def _formats(help_text: str, output_mapping: CapabilityMapping) -> list[OutputFormat]:
    formats = []
    lowered = help_text.lower()
    for output_format in (OutputFormat.JSONL, OutputFormat.JSON, OutputFormat.CSV):
        dedicated = f"--{output_format.value}" in output_mapping.aliases_observed
        mentioned = bool(re.search(rf"\b{output_format.value}\b", lowered))
        if dedicated or mentioned:
            formats.append(output_format)
    return formats


def inspect_llama_bench(
    binary_path: Path | None = None,
    *,
    plan: SearchPlan | None = None,
    timeout_seconds: float = 5.0,
    requested_format: OutputFormat | None = None,
    include_probe_output: bool = False,
    use_cache: bool = True,
) -> LlamaBenchCapabilities:
    binary = resolve_llama_bench_binary(binary_path, plan=plan)
    metadata = binary.stat()
    key = (str(binary), metadata.st_size, metadata.st_mtime_ns)
    with _LOCK:
        cached = _CACHE.get(key) if use_cache else None
    if cached is not None and requested_format in {None, cached.output.selected_format}:
        if include_probe_output:
            return cached
        return cached.model_copy(
            update={
                "version_probe": cached.version_probe.model_copy(
                    update={"stdout": None, "stderr": None}
                ),
                "help_probe": cached.help_probe.model_copy(update={"stdout": None, "stderr": None}),
            }
        )
    version_probe = _probe(binary, "--version", timeout_seconds)
    help_probe = _probe(binary, "--help", timeout_seconds)
    if not help_probe.successful:
        raise BenchCapabilityError(help_probe.error or "llama-bench --help probe failed")
    help_text = f"{help_probe.stdout or ''}\n{help_probe.stderr or ''}"
    tokens = parse_option_tokens(help_text)
    mappings = {name: _mapping(name, tokens) for name in _ALIASES}
    supported_formats = _formats(help_text, mappings["output_format"])
    if not mappings["model_path"].supported:
        raise BenchCapabilityError("llama-bench help exposes no supported model-path flag")
    if not mappings["output_format"].supported or not supported_formats:
        raise BenchCapabilityError(
            "llama-bench exposes no supported JSONL, JSON, or CSV machine-readable output"
        )
    preference = [OutputFormat.JSONL, OutputFormat.JSON, OutputFormat.CSV]
    selected = requested_format or next(item for item in preference if item in supported_formats)
    if selected not in supported_formats:
        raise BenchCapabilityError(f"Requested output format {selected.value} is not supported")
    version_text = f"{version_probe.stdout or ''}\n{version_probe.stderr or ''}"
    version = (
        next((line.strip() for line in version_text.splitlines() if line.strip()), None)
        if version_probe.successful
        else None
    )
    capabilities = LlamaBenchCapabilities(
        binary_path=binary,
        binary_sha256=hash_file_streaming(binary),
        binary_size=metadata.st_size,
        binary_mtime_ns=metadata.st_mtime_ns,
        version=version,
        raw_option_tokens=sorted(tokens),
        mappings=mappings,
        output=OutputFormatSelection(
            requested_format=requested_format,
            selected_format=selected,
            supported_formats=supported_formats,
            selection_reason="Preferred JSONL, then JSON, then CSV from inspected help output",
        ),
        version_probe=version_probe,
        help_probe=help_probe,
        synthetic_fixture="synthetic" in (version or "").lower(),
    )
    with _LOCK:
        _CACHE[key] = capabilities
    if include_probe_output:
        return capabilities
    return capabilities.model_copy(
        update={
            "version_probe": version_probe.model_copy(update={"stdout": None, "stderr": None}),
            "help_probe": help_probe.model_copy(update={"stdout": None, "stderr": None}),
        }
    )
