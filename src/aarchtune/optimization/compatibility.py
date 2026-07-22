"""Baseline loading and current-system provenance compatibility checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue, ValidationError

from aarchtune.baseline.models import BaselineManifest, FileProvenance
from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.hardware.detector import detect_hardware
from aarchtune.models import HardwareReport
from aarchtune.optimization.errors import (
    BaselineReferenceError,
    ProvenanceMismatchError,
)
from aarchtune.optimization.identity import stable_hash
from aarchtune.optimization.models import (
    BaselineReference,
    CompatibilityClass,
    CompatibilityDifference,
    HardwareFingerprint,
    ModelFingerprint,
    ProfileRuntime,
    ProvenanceCompatibility,
    RuntimeFingerprint,
    SearchPlanInput,
    WorkloadFingerprint,
)
from aarchtune.runtime.capabilities import (
    ServerCapabilities,
    inspect_llama_server_capabilities,
)
from aarchtune.runtime.command import CommandBuildResult
from aarchtune.workload.loader import load_workload, summarize_workload
from aarchtune.workload.schema import WorkloadValidationSummary

SUPPORTED_SCHEMA_VERSION = "1.0"
REQUIRED_BASELINE_FILES = (
    "manifest.json",
    "hardware.json",
    "runtime-inspection.json",
    "server-command.json",
    "model.json",
    "workload.json",
    "baseline-summary.json",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineReferenceError(f"Cannot read baseline artifact {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BaselineReferenceError(f"Baseline artifact root is not an object: {path}")
    if raw.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise BaselineReferenceError(
            f"Unsupported schema_version in {path.name}: {raw.get('schema_version')!r}"
        )
    return raw


def _envelope_data(path: Path) -> dict[str, Any]:
    raw = _read_json(path)
    data = raw.get("data")
    if not isinstance(data, dict):
        raise BaselineReferenceError(f"Baseline envelope has no object data: {path.name}")
    return data


def hardware_fingerprint(report: HardwareReport, *, synthetic: bool = False) -> HardwareFingerprint:
    semantic = {
        "architecture": report.architecture,
        "is_arm64": report.is_arm64,
        "cpu_model": report.cpu_model,
        "logical_cores": report.logical_cores,
        "physical_cores": report.physical_cores,
        "total_memory_bytes": report.memory_bytes,
        "numa_nodes": report.numa_nodes,
        "features": report.features.model_dump(mode="json"),
        "synthetic_fixture": synthetic,
    }
    return HardwareFingerprint(
        architecture=report.architecture,
        is_arm64=report.is_arm64,
        cpu_model=report.cpu_model,
        logical_cores=report.logical_cores,
        physical_cores=report.physical_cores,
        total_memory_bytes=report.memory_bytes,
        available_memory_bytes=report.memory_available_bytes,
        numa_nodes=report.numa_nodes,
        features=report.features,
        synthetic_fixture=synthetic,
        fingerprint_hash=stable_hash(semantic),
    )


def runtime_fingerprint(capabilities: ServerCapabilities) -> RuntimeFingerprint:
    digest = hash_file_streaming(capabilities.binary_path)
    semantic = {
        "binary_sha256": digest,
        "binary_size": capabilities.binary_size,
        "version": capabilities.version,
        "supported_flags": sorted(capabilities.supported_flags),
        "kleidiai_status": capabilities.kleidiai_status.value,
    }
    return RuntimeFingerprint(
        binary_path=capabilities.binary_path,
        binary_sha256=digest,
        binary_size=capabilities.binary_size,
        binary_mtime_ns=capabilities.binary_mtime_ns,
        version=capabilities.version,
        supported_flags=sorted(capabilities.supported_flags),
        kleidiai_status=capabilities.kleidiai_status.value,
        fingerprint_hash=stable_hash(semantic),
    )


def _model_fingerprint(path: Path, synthetic: bool) -> ModelFingerprint:
    resolved = path.expanduser().resolve()
    metadata = resolved.stat()
    return ModelFingerprint(
        path=resolved,
        filename=resolved.name,
        size_bytes=metadata.st_size,
        sha256=hash_file_streaming(resolved),
        synthetic_fixture=synthetic,
    )


def _workload_fingerprint(path: Path) -> WorkloadFingerprint:
    loaded = load_workload(path)
    summary = summarize_workload(loaded)
    return WorkloadFingerprint(
        path=loaded.path.resolve(),
        sha256=loaded.sha256,
        task_count=summary.tasks,
        category_count=summary.categories,
        validator_count=summary.validators,
        deterministic=summary.deterministic,
    )


def _runtime_from_command(
    command: CommandBuildResult, binary_path: Path, backend: str
) -> ProfileRuntime:
    settings = command.requested_settings

    def integer(name: str) -> int | None:
        value = settings.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    numa = settings.get("numa_mode", "disabled")
    if numa not in {"disabled", "distribute", "isolate", "numactl"}:
        raise BaselineReferenceError(f"Unsupported baseline NUMA mode: {numa!r}")
    return ProfileRuntime(
        backend_label=backend,
        binary_path=binary_path,
        threads=integer("threads"),
        threads_batch=integer("threads_batch"),
        batch_size=integer("batch_size"),
        ubatch_size=integer("ubatch_size"),
        context_size=integer("context_size"),
        parallel_slots=integer("parallel_slots"),
        prompt_cache=settings.get("prompt_cache") is True,
        mmap=settings.get("mmap") is not False,
        numa_mode=cast(Any, numa),
        cpu_affinity_policy="none",
    )


def _difference(
    differences: list[CompatibilityDifference],
    field: str,
    baseline: JsonValue,
    current: JsonValue,
    severity: str,
    reason: str,
) -> None:
    if baseline != current:
        differences.append(
            CompatibilityDifference(
                field=field,
                baseline_value=baseline,
                current_value=current,
                severity=cast(Any, severity),
                reason=reason,
            )
        )


def _compare(
    baseline_hardware: HardwareReport,
    current_hardware: HardwareReport,
    baseline_capabilities: ServerCapabilities,
    current_runtime: RuntimeFingerprint,
    baseline_binary_hash: str,
    baseline_model_hash: str,
    current_model: ModelFingerprint,
    baseline_workload_hash: str,
    current_workload: WorkloadFingerprint,
    *,
    allow_runtime_change: bool,
) -> ProvenanceCompatibility:
    differences: list[CompatibilityDifference] = []
    _difference(
        differences,
        "architecture",
        baseline_hardware.architecture,
        current_hardware.architecture,
        "incompatible",
        "Architecture changes invalidate hardware-specific planning",
    )
    _difference(
        differences,
        "cpu_model",
        baseline_hardware.cpu_model,
        current_hardware.cpu_model,
        "warning",
        "CPU model differs from the baseline machine",
    )
    for field in ("physical_cores", "logical_cores", "numa_nodes", "memory_bytes"):
        _difference(
            differences,
            field,
            cast(JsonValue, getattr(baseline_hardware, field)),
            cast(JsonValue, getattr(current_hardware, field)),
            "warning",
            f"Current {field} differs from baseline",
        )
    _difference(
        differences,
        "memory_available_bytes",
        cast(JsonValue, baseline_hardware.memory_available_bytes),
        cast(JsonValue, current_hardware.memory_available_bytes),
        "warning",
        "Currently available memory differs from the baseline observation",
    )
    _difference(
        differences,
        "cpu_features",
        cast(JsonValue, baseline_hardware.features.model_dump(mode="json")),
        cast(JsonValue, current_hardware.features.model_dump(mode="json")),
        "warning",
        "CPU feature set differs from baseline",
    )
    runtime_severity = "overridden" if allow_runtime_change else "incompatible"
    _difference(
        differences,
        "runtime_binary_sha256",
        baseline_binary_hash,
        current_runtime.binary_sha256,
        runtime_severity,
        "Runtime binary content differs from baseline",
    )
    _difference(
        differences,
        "runtime_version",
        baseline_capabilities.version,
        current_runtime.version,
        runtime_severity,
        "Runtime version differs from baseline",
    )
    _difference(
        differences,
        "supported_flags",
        cast(JsonValue, sorted(baseline_capabilities.supported_flags)),
        cast(JsonValue, current_runtime.supported_flags),
        runtime_severity,
        "Runtime capability set differs from baseline",
    )
    _difference(
        differences,
        "model_sha256",
        baseline_model_hash,
        current_model.sha256,
        "incompatible",
        "Model hash must match the baseline",
    )
    _difference(
        differences,
        "workload_sha256",
        baseline_workload_hash,
        current_workload.sha256,
        "incompatible",
        "Workload exact-byte hash must match the baseline",
    )
    incompatible = any(item.severity == "incompatible" for item in differences)
    overrides = sorted({item.field for item in differences if item.severity == "overridden"})
    if incompatible:
        classification = CompatibilityClass.INCOMPATIBLE
    elif differences:
        classification = CompatibilityClass.COMPATIBLE_WITH_WARNINGS
    else:
        classification = CompatibilityClass.IDENTICAL
    return ProvenanceCompatibility(
        classification=classification,
        differences=differences,
        overrides=overrides,
    )


def load_baseline_input(
    baseline_dir: Path,
    *,
    allow_synthetic: bool,
    allow_runtime_change: bool,
) -> SearchPlanInput:
    root = baseline_dir.expanduser().resolve()
    missing = [name for name in REQUIRED_BASELINE_FILES if not (root / name).is_file()]
    if missing:
        raise BaselineReferenceError(
            f"Baseline is missing required artifacts: {', '.join(missing)}"
        )
    try:
        manifest = BaselineManifest.model_validate_json(
            json.dumps(_read_json(root / "manifest.json"))
        )
        summary_data = _read_json(root / "baseline-summary.json")
        baseline_hardware = HardwareReport.model_validate_json(
            json.dumps(_envelope_data(root / "hardware.json"))
        )
        runtime_data = _envelope_data(root / "runtime-inspection.json")
        baseline_capabilities = ServerCapabilities.model_validate_json(
            json.dumps(runtime_data["capabilities"])
        )
        binary_info = FileProvenance.model_validate_json(json.dumps(runtime_data["binary"]))
        model_info = FileProvenance.model_validate_json(
            json.dumps(_envelope_data(root / "model.json"))
        )
        workload_info = WorkloadValidationSummary.model_validate_json(
            json.dumps(_envelope_data(root / "workload.json"))
        )
        command_data = _envelope_data(root / "server-command.json")
        command = CommandBuildResult.model_validate_json(json.dumps(command_data["command"]))
    except (ValidationError, KeyError) as exc:
        raise BaselineReferenceError(f"Baseline artifact schema validation failed: {exc}") from exc
    summary_status = summary_data.get("status")
    synthetic_fixture = summary_data.get("synthetic_fixture")
    if not isinstance(synthetic_fixture, bool):
        raise BaselineReferenceError("Baseline summary synthetic_fixture must be boolean")
    if manifest.status.value != "completed" or summary_status != "completed":
        raise BaselineReferenceError(
            f"Baseline must be completed; manifest status is {manifest.status.value!r}"
        )
    if synthetic_fixture and not allow_synthetic:
        raise BaselineReferenceError(
            "Synthetic baseline evidence requires --allow-synthetic for development planning"
        )
    for label, information in (("runtime binary", binary_info), ("model", model_info)):
        if not information.hash.completed or not information.hash.value:
            raise BaselineReferenceError(f"Baseline {label} SHA-256 is unavailable")
    binary_path = Path(binary_info.path)
    model_path = Path(model_info.path)
    workload_path = workload_info.path
    current_capabilities = inspect_llama_server_capabilities(binary_path, include_probe_output=True)
    current_hardware = detect_hardware(model_path=model_path)
    current_runtime = runtime_fingerprint(current_capabilities)
    current_model = _model_fingerprint(model_path, synthetic_fixture)
    current_workload = _workload_fingerprint(workload_path)
    baseline_binary_hash = binary_info.hash.value
    baseline_model_hash = model_info.hash.value
    if baseline_binary_hash is None or baseline_model_hash is None:
        raise BaselineReferenceError("Mandatory baseline provenance hash became unavailable")
    compatibility = _compare(
        baseline_hardware,
        current_hardware,
        baseline_capabilities,
        current_runtime,
        baseline_binary_hash,
        baseline_model_hash,
        current_model,
        workload_info.sha256,
        current_workload,
        allow_runtime_change=allow_runtime_change,
    )
    if compatibility.classification is CompatibilityClass.INCOMPATIBLE:
        fields = ", ".join(
            item.field for item in compatibility.differences if item.severity == "incompatible"
        )
        raise ProvenanceMismatchError(f"Baseline provenance is incompatible: {fields}")
    manifest_bytes = (root / "manifest.json").read_bytes()
    baseline_reference = BaselineReference(
        path=root,
        run_id=manifest.run_id,
        status=manifest.status.value,
        synthetic_fixture=synthetic_fixture,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        compatibility=compatibility,
    )
    backend = "KleidiAI" if current_runtime.kleidiai_status == "verified" else "llama.cpp"
    baseline_runtime = _runtime_from_command(command, binary_path.resolve(), backend)
    process_data = summary_data.get("process")
    peak_data = (
        process_data.get("measured_phase_peak_rss_bytes")
        if isinstance(process_data, dict)
        else None
    )
    peak_value = peak_data.get("value") if isinstance(peak_data, dict) else None
    peak_available = peak_data.get("available") if isinstance(peak_data, dict) else False
    peak = (
        peak_value
        if peak_available is True
        and isinstance(peak_value, int)
        and not isinstance(peak_value, bool)
        and peak_value >= 0
        else None
    )
    return SearchPlanInput(
        source="baseline",
        baseline=baseline_reference,
        hardware=hardware_fingerprint(current_hardware),
        runtime=current_runtime,
        model=current_model,
        workload=current_workload,
        baseline_runtime=baseline_runtime,
        baseline_peak_rss_bytes=peak,
        overrides=compatibility.overrides,
    )


def load_explicit_input(binary: Path, model: Path, workload: Path) -> SearchPlanInput:
    capabilities = inspect_llama_server_capabilities(binary, include_probe_output=True)
    hardware = detect_hardware(model_path=model)
    runtime = runtime_fingerprint(capabilities)
    synthetic = capabilities.version is not None and "synthetic" in capabilities.version.lower()
    model_fingerprint = _model_fingerprint(model, synthetic)
    workload_fingerprint = _workload_fingerprint(workload)
    backend = "KleidiAI" if runtime.kleidiai_status == "verified" else "llama.cpp"
    baseline_runtime = ProfileRuntime(
        backend_label=backend,
        binary_path=runtime.binary_path,
        prompt_cache=False,
        mmap=True,
        numa_mode="disabled",
        cpu_affinity_policy="none",
    )
    return SearchPlanInput(
        source="explicit",
        baseline=None,
        hardware=hardware_fingerprint(hardware),
        runtime=runtime,
        model=model_fingerprint,
        workload=workload_fingerprint,
        baseline_runtime=baseline_runtime,
        baseline_peak_rss_bytes=None,
        overrides=[],
    )
