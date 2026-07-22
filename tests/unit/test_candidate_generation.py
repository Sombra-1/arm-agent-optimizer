from __future__ import annotations

import json
from pathlib import Path

import pytest

from aarchtune.models import CPUFeatures
from aarchtune.optimization.errors import PlanningError
from aarchtune.optimization.generator import generate_candidates, generate_thread_values
from aarchtune.optimization.models import (
    HardwareFingerprint,
    ModelFingerprint,
    OptimizationGoal,
    ProfileRuntime,
    RuntimeFingerprint,
    SearchPlanInput,
    WorkloadFingerprint,
)
from aarchtune.optimization.search_space import load_search_space
from aarchtune.runtime.capabilities import ServerCapabilities


def _input(
    fake_binary: Path,
    fake_model: Path,
    *,
    physical: int | None = 16,
    logical: int | None = 16,
    numa: int | None = 1,
    available_memory: int | None = 32 * 1024**3,
    peak_rss: int | None = 2 * 1024**3,
) -> SearchPlanInput:
    hardware = HardwareFingerprint(
        architecture="aarch64",
        is_arm64=True,
        cpu_model="Synthetic planning CPU",
        logical_cores=logical,
        physical_cores=physical,
        total_memory_bytes=64 * 1024**3,
        available_memory_bytes=available_memory,
        numa_nodes=numa,
        features=CPUFeatures(asimd=True, dotprod=True, i8mm=True, sve=True),
        synthetic_fixture=True,
        fingerprint_hash="hardware",
    )
    runtime = RuntimeFingerprint(
        binary_path=fake_binary,
        binary_sha256="a" * 64,
        binary_size=fake_binary.stat().st_size,
        binary_mtime_ns=fake_binary.stat().st_mtime_ns,
        version="synthetic",
        supported_flags=[],
        kleidiai_status="unknown",
        fingerprint_hash="runtime",
    )
    baseline = ProfileRuntime(
        backend_label="llama.cpp",
        binary_path=fake_binary,
        threads=min(8, logical or 8),
        threads_batch=min(16, logical or 16),
        batch_size=512,
        ubatch_size=128,
        context_size=4096,
        parallel_slots=1,
        prompt_cache=False,
        mmap=True,
    )
    return SearchPlanInput(
        source="explicit",
        baseline=None,
        hardware=hardware,
        runtime=runtime,
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


@pytest.mark.parametrize("goal", list(OptimizationGoal))
def test_generation_is_deterministic_unique_and_bounded(
    fake_binary: Path,
    fake_model: Path,
    server_capabilities: ServerCapabilities,
    goal: OptimizationGoal,
) -> None:
    plan_input = _input(fake_binary, fake_model)
    search = load_search_space().configuration
    first = generate_candidates(plan_input, server_capabilities, search, goal, 10)
    second = generate_candidates(plan_input, server_capabilities, search, goal, 10)
    assert [item.model_dump() for item in first.candidates] == [
        item.model_dump() for item in second.candidates
    ]
    assert len(first.candidates) <= 10
    assert first.candidates[0].id == "baseline"
    assert len({item.id for item in first.candidates}) == len(first.candidates)
    assert len({item.profile_hash for item in first.candidates}) == len(first.candidates)


def test_candidate_constraints_rationales_and_sources(
    fake_binary: Path, fake_model: Path, server_capabilities: ServerCapabilities
) -> None:
    result = generate_candidates(
        _input(fake_binary, fake_model),
        server_capabilities,
        load_search_space().configuration,
        OptimizationGoal.BALANCED,
        24,
    )
    for candidate in result.candidates:
        assert candidate.rationale
        assert set(candidate.parameter_sources) == set(type(candidate.runtime).model_fields)
        assert candidate.runtime.threads is None or candidate.runtime.threads > 0
        assert candidate.runtime.threads is None or candidate.runtime.threads <= 16
        assert (
            candidate.runtime.ubatch_size is None
            or candidate.runtime.batch_size is None
            or (candidate.runtime.ubatch_size <= candidate.runtime.batch_size)
        )
        assert candidate.runtime.context_size == 4096


def test_goals_produce_meaningfully_different_parallel_and_batch_coverage(
    fake_binary: Path, fake_model: Path, server_capabilities: ServerCapabilities
) -> None:
    search = load_search_space().configuration
    plan_input = _input(fake_binary, fake_model)
    latency = generate_candidates(
        plan_input, server_capabilities, search, OptimizationGoal.LATENCY, 24
    )
    throughput = generate_candidates(
        plan_input, server_capabilities, search, OptimizationGoal.THROUGHPUT, 24
    )
    memory = generate_candidates(
        plan_input, server_capabilities, search, OptimizationGoal.MEMORY, 24
    )
    latency_slots = {item.runtime.parallel_slots for item in latency.candidates}
    throughput_slots = {item.runtime.parallel_slots for item in throughput.candidates}
    assert 4 not in latency_slots
    assert 4 in throughput_slots
    assert max(item.runtime.batch_size or 0 for item in throughput.candidates) >= 1024
    assert min(item.runtime.batch_size or 10**9 for item in memory.candidates) <= 128


def test_low_core_parallelism_is_excluded(
    fake_binary: Path, fake_model: Path, server_capabilities: ServerCapabilities
) -> None:
    result = generate_candidates(
        _input(fake_binary, fake_model, physical=4, logical=4),
        server_capabilities,
        load_search_space().configuration,
        OptimizationGoal.THROUGHPUT,
        24,
    )
    assert all((item.runtime.parallel_slots or 1) <= 2 for item in result.candidates)
    assert any(
        item.reason_code == "insufficient_cores_for_parallelism" for item in result.exclusions
    )


def test_missing_physical_cores_falls_back_to_logical(fake_binary: Path, fake_model: Path) -> None:
    plan_input = _input(fake_binary, fake_model, physical=None, logical=12)
    values = generate_thread_values(plan_input, load_search_space().configuration)
    assert max(values) == 12
    assert any(source.source.value == "fraction_of_logical_cores" for source in values.values())


def test_limited_runtime_excludes_unsupported_experiments(
    fake_binary: Path,
    fake_model: Path,
    server_capabilities: ServerCapabilities,
) -> None:
    limited = server_capabilities.model_copy(
        update={
            "raw_option_tokens": {
                "--model",
                "--host",
                "--port",
                "--threads",
                "--threads-batch",
                "--batch-size",
                "--ubatch-size",
                "--ctx-size",
                "--parallel",
                "--no-mmap",
            },
            "supported_flags": {
                "--model",
                "--host",
                "--port",
                "--threads",
                "--threads-batch",
                "--batch-size",
                "--ubatch-size",
                "--ctx-size",
                "--parallel",
                "--no-mmap",
            },
        }
    )
    result = generate_candidates(
        _input(fake_binary, fake_model),
        limited,
        load_search_space().configuration,
        OptimizationGoal.BALANCED,
        24,
    )
    assert result.candidates
    assert any(item.reason_code == "unsupported_configuration" for item in result.exclusions)
    assert all(item.compatibility.compatible for item in result.candidates)


def test_incompatible_baseline_fails_instead_of_disappearing(
    fake_binary: Path,
    fake_model: Path,
    server_capabilities: ServerCapabilities,
) -> None:
    limited = server_capabilities.model_copy(
        update={"raw_option_tokens": {"--model", "--host", "--port"}}
    )
    with pytest.raises(PlanningError, match="baseline configuration"):
        generate_candidates(
            _input(fake_binary, fake_model),
            limited,
            load_search_space().configuration,
            OptimizationGoal.BALANCED,
            24,
        )


def test_diversity_limit_preserves_baseline_and_dimension_extremes(
    fake_binary: Path, fake_model: Path, server_capabilities: ServerCapabilities
) -> None:
    result = generate_candidates(
        _input(fake_binary, fake_model),
        server_capabilities,
        load_search_space().configuration,
        OptimizationGoal.BALANCED,
        8,
    )
    assert len(result.candidates) == 8
    assert result.candidates[0].baseline
    assert len({item.stage for item in result.candidates}) >= 4
    assert min(item.runtime.threads or 999 for item in result.candidates) <= 4
    assert max(item.runtime.threads or 0 for item in result.candidates) >= 12


def test_minimum_profile_warning_does_not_fabricate_candidates(
    fake_binary: Path, fake_model: Path, server_capabilities: ServerCapabilities
) -> None:
    result = generate_candidates(
        _input(fake_binary, fake_model),
        server_capabilities,
        load_search_space().configuration,
        OptimizationGoal.BALANCED,
        1,
    )
    assert [item.id for item in result.candidates] == ["baseline"]
    assert any(item.code == "minimum_profile_count_unmet" for item in result.warnings)


def test_explicit_context_only_preserves_or_increases_baseline(
    fake_binary: Path, fake_model: Path, server_capabilities: ServerCapabilities
) -> None:
    search = load_search_space().configuration
    search = search.model_copy(
        update={
            "context": search.context.model_copy(
                update={"policy": "explicit", "explicit_sizes": [2048, 8192]}
            )
        }
    )
    result = generate_candidates(
        _input(fake_binary, fake_model),
        server_capabilities,
        search,
        OptimizationGoal.BALANCED,
        24,
    )
    assert all((item.runtime.context_size or 4096) >= 4096 for item in result.candidates)
    assert any(item.runtime.context_size == 8192 for item in result.candidates)
    assert any(item.reason_code == "context_below_baseline" for item in result.exclusions)


def test_numa_alternative_requires_multiple_nodes_and_explicit_enablement(
    fake_binary: Path, fake_model: Path, server_capabilities: ServerCapabilities
) -> None:
    search = load_search_space().configuration.model_copy(update={"enable_numa_experiments": True})
    result = generate_candidates(
        _input(fake_binary, fake_model, numa=2),
        server_capabilities,
        search,
        OptimizationGoal.BALANCED,
        24,
    )
    assert any(item.runtime.numa_mode == "distribute" for item in result.candidates)


def test_all_required_synthetic_planning_fixtures_are_labelled() -> None:
    fixture_dir = Path(__file__).resolve().parents[1] / "fixtures/planning"
    expected = {
        "aarch64-4-core",
        "aarch64-16-core",
        "aarch64-64-core",
        "aarch64-two-numa",
        "x86-development",
        "low-memory-arm64",
        "missing-physical-core-count",
        "runtime-limited-flags",
        "runtime-full-flags",
    }
    records = [json.loads(path.read_text()) for path in fixture_dir.glob("*.json")]
    assert {record["name"] for record in records} == expected
    assert all(record["synthetic_fixture"] is True for record in records)
