from __future__ import annotations

from pathlib import Path

import pytest

from aarchtune.models import CPUFeatures
from aarchtune.optimization.compatibility_checks import check_candidate_compatibility
from aarchtune.optimization.memory import assess_memory_risk
from aarchtune.optimization.models import (
    HardwareFingerprint,
    MemoryRiskClass,
    ModelFingerprint,
    ProfileRuntime,
    RuntimeFingerprint,
    SearchPlanInput,
    WorkloadFingerprint,
)
from aarchtune.runtime.capabilities import ServerCapabilities


def _input(
    fake_binary: Path,
    fake_model: Path,
    *,
    available_memory: int | None = 32 * 1024**3,
    peak_rss: int | None = 2 * 1024**3,
) -> SearchPlanInput:
    baseline = ProfileRuntime(
        backend_label="llama.cpp",
        binary_path=fake_binary,
        threads=8,
        threads_batch=16,
        batch_size=512,
        ubatch_size=128,
        context_size=4096,
        parallel_slots=1,
    )
    return SearchPlanInput(
        source="explicit",
        baseline=None,
        hardware=HardwareFingerprint(
            architecture="aarch64",
            is_arm64=True,
            cpu_model="Synthetic",
            logical_cores=16,
            physical_cores=16,
            total_memory_bytes=64 * 1024**3,
            available_memory_bytes=available_memory,
            numa_nodes=1,
            features=CPUFeatures(asimd=True),
            synthetic_fixture=True,
            fingerprint_hash="hardware",
        ),
        runtime=RuntimeFingerprint(
            binary_path=fake_binary,
            binary_sha256="a" * 64,
            binary_size=fake_binary.stat().st_size,
            binary_mtime_ns=fake_binary.stat().st_mtime_ns,
            version="synthetic",
            supported_flags=[],
            kleidiai_status="unknown",
            fingerprint_hash="runtime",
        ),
        model=ModelFingerprint(
            path=fake_model,
            filename=fake_model.name,
            size_bytes=100 * 1024**2,
            sha256="b" * 64,
            synthetic_fixture=True,
        ),
        workload=WorkloadFingerprint(
            path=Path("workload.jsonl"),
            sha256="c" * 64,
            task_count=5,
            category_count=5,
            validator_count=20,
            deterministic=True,
        ),
        baseline_runtime=baseline,
        baseline_peak_rss_bytes=peak_rss,
        overrides=[],
    )


def test_memory_guardrail_safe_with_baseline_headroom(fake_binary: Path, fake_model: Path) -> None:
    plan_input = _input(fake_binary, fake_model)
    result = assess_memory_risk(plan_input.baseline_runtime, plan_input)
    assert result.classification is MemoryRiskClass.SAFE
    assert result.estimated_memory_bytes is None
    assert result.available is False
    assert result.method == "baseline-relative-guardrail"


def test_memory_guardrail_unknown_without_peak_or_available_memory(
    fake_binary: Path, fake_model: Path
) -> None:
    missing_peak = _input(fake_binary, fake_model, peak_rss=None)
    missing_memory = _input(fake_binary, fake_model, available_memory=None)
    assert (
        assess_memory_risk(missing_peak.baseline_runtime, missing_peak).classification
        is MemoryRiskClass.UNKNOWN
    )
    assert (
        assess_memory_risk(missing_memory.baseline_runtime, missing_memory).classification
        is MemoryRiskClass.UNKNOWN
    )


def test_high_parallelism_low_headroom_is_warning_or_high_risk(
    fake_binary: Path, fake_model: Path
) -> None:
    plan_input = _input(
        fake_binary,
        fake_model,
        available_memory=5 * 1024**3,
        peak_rss=2 * 1024**3,
    )
    runtime = plan_input.baseline_runtime.model_copy(update={"parallel_slots": 4})
    result = assess_memory_risk(runtime, plan_input)
    assert result.classification is MemoryRiskClass.HIGH_RISK
    assert "parallelism" in result.reason.lower()


def test_context_increase_raises_relative_risk_deterministically(
    fake_binary: Path, fake_model: Path
) -> None:
    plan_input = _input(
        fake_binary,
        fake_model,
        available_memory=6 * 1024**3,
        peak_rss=2 * 1024**3,
    )
    runtime = plan_input.baseline_runtime.model_copy(update={"context_size": 8192})
    first = assess_memory_risk(runtime, plan_input)
    second = assess_memory_risk(runtime, plan_input)
    assert first == second
    assert first.classification in {MemoryRiskClass.WARNING, MemoryRiskClass.HIGH_RISK}
    assert "not exact" in " ".join(first.assumptions).lower()


def test_fully_compatible_candidate_has_complete_mapping_details(
    fake_binary: Path, server_capabilities: ServerCapabilities
) -> None:
    runtime = ProfileRuntime(
        backend_label="llama.cpp",
        binary_path=fake_binary,
        threads=8,
        threads_batch=8,
        batch_size=512,
        ubatch_size=128,
        context_size=4096,
        parallel_slots=2,
        prompt_cache=True,
        mmap=False,
        numa_mode="distribute",
    )
    result = check_candidate_compatibility(runtime, server_capabilities)
    assert result.compatible is True
    assert {detail.field for detail in result.details} >= {
        "threads",
        "batch_size",
        "parallel_slots",
        "prompt_cache",
        "mmap",
        "numa_mode",
    }


@pytest.mark.parametrize(
    ("update", "missing_flag"),
    [
        ({"prompt_cache": True}, "--cache-prompt"),
        ({"parallel_slots": 2}, "--parallel"),
        ({"mmap": False}, "--no-mmap"),
    ],
)
def test_unsupported_settings_are_not_silently_removed(
    fake_binary: Path,
    server_capabilities: ServerCapabilities,
    update: dict[str, object],
    missing_flag: str,
) -> None:
    limited = server_capabilities.model_copy(
        update={"raw_option_tokens": {"--model", "--host", "--port"}}
    )
    runtime = ProfileRuntime(
        backend_label="llama.cpp", binary_path=fake_binary, mmap=True
    ).model_copy(update=update)
    result = check_candidate_compatibility(runtime, limited)
    assert result.compatible is False
    assert missing_flag in result.unsupported_flags
    assert any(detail.requested_value == next(iter(update.values())) for detail in result.details)


def test_affinity_remains_none_without_safe_mapping(
    fake_binary: Path, server_capabilities: ServerCapabilities
) -> None:
    runtime = ProfileRuntime(
        backend_label="llama.cpp",
        binary_path=fake_binary,
        mmap=True,
        cpu_affinity_policy="compact",
    )
    result = check_candidate_compatibility(runtime, server_capabilities)
    assert result.compatible is False
    assert any(detail.field == "cpu_affinity_policy" for detail in result.details)
