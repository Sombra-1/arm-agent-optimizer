"""Best-effort Linux hardware and runtime detection."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

import psutil

from aarchtune.hardware.features import arm_features_from_flags, normalize_cpu_flags
from aarchtune.models import HardwareReport, ModelFileInspection
from aarchtune.runtime.discovery import discover_llama_cpp

_CPUINFO_PATH = Path("/proc/cpuinfo")


def normalize_architecture(machine: str) -> str:
    """Normalize common Arm64 architecture labels without relabeling other hosts."""

    lowered = machine.strip().lower()
    return "aarch64" if lowered in {"aarch64", "arm64"} else lowered or "unknown"


def parse_cpuinfo(text: str) -> tuple[set[str], str | None]:
    """Extract CPU flags and the most useful available model label."""

    flags: set[str] = set()
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized_key = key.strip().lower()
        normalized_value = value.strip()
        if normalized_key in {"features", "flags"}:
            flags.update(normalized_value.split())
        if normalized_key in {"model name", "hardware", "processor"} and normalized_value:
            values.setdefault(normalized_key, normalized_value)

    model = values.get("model name") or values.get("hardware") or values.get("processor")
    return normalize_cpu_flags(flags), model


def _read_cpuinfo(path: Path = _CPUINFO_PATH) -> tuple[set[str], str | None]:
    try:
        return parse_cpuinfo(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return set(), None


def _read_lscpu() -> dict[str, str] | None:
    try:
        result = subprocess.run(
            ["lscpu", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        payload: Any = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    rows = payload.get("lscpu") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None

    parsed: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        field = row.get("field")
        data = row.get("data")
        if isinstance(field, str) and isinstance(data, (str, int, float)):
            parsed[field.rstrip(":")] = str(data)
    return parsed or None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def inspect_model_file(path: Path) -> ModelFileInspection:
    """Check that a requested model is a readable regular file."""

    expanded = path.expanduser()
    if not expanded.is_file():
        return ModelFileInspection(path=expanded, readable=False, error="Not a regular file")
    if not os.access(expanded, os.R_OK):
        return ModelFileInspection(path=expanded, readable=False, error="File is not readable")
    try:
        size = expanded.stat().st_size
    except OSError as exc:
        return ModelFileInspection(path=expanded, readable=False, error=f"Cannot stat file: {exc}")
    return ModelFileInspection(path=expanded.resolve(), readable=True, size_bytes=size)


def detect_hardware(*, model_path: Path | None = None) -> HardwareReport:
    """Collect a serializable report using only local, read-only inspection."""

    architecture = normalize_architecture(platform.machine())
    cpu_flags, cpuinfo_model = _read_cpuinfo()
    lscpu = _read_lscpu()
    lscpu_model = lscpu.get("Model name") if lscpu else None
    memory = psutil.virtual_memory()

    return HardwareReport(
        architecture=architecture,
        is_arm64=architecture == "aarch64",
        operating_system=platform.system(),
        kernel=platform.release(),
        cpu_model=lscpu_model or cpuinfo_model,
        logical_cores=psutil.cpu_count(logical=True),
        physical_cores=psutil.cpu_count(logical=False),
        memory_bytes=memory.total,
        memory_available_bytes=memory.available,
        numa_nodes=_parse_int(lscpu.get("NUMA node(s)")) if lscpu else None,
        features=arm_features_from_flags(cpu_flags),
        cpu_flags=sorted(cpu_flags),
        lscpu=lscpu,
        llama_cpp=discover_llama_cpp(),
        model=inspect_model_file(model_path) if model_path else None,
    )
