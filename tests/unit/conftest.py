from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from aarchtune.optimization.models import OptimizationGoal
from aarchtune.optimization.planner import create_search_plan
from aarchtune.runtime.capabilities import (
    ServerCapabilities,
    clear_capability_cache,
    inspect_llama_server_capabilities,
)
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.screening.capabilities import clear_bench_capability_cache, inspect_llama_bench
from aarchtune.screening.models import LlamaBenchCapabilities, ScreeningConfig
from aarchtune.screening.runner import run_screening


@pytest.fixture
def fake_binary() -> Path:
    return (Path(__file__).resolve().parents[1] / "fixtures/bin/fake-llama-server").resolve()


@pytest.fixture
def fake_model() -> Path:
    return (Path(__file__).resolve().parents[1] / "fixtures/models/fake-model.gguf").resolve()


@pytest.fixture
def fake_bench() -> Path:
    return (Path(__file__).resolve().parents[1] / "fixtures/bin/fake-llama-bench").resolve()


@pytest.fixture
def bench_capabilities(fake_bench: Path, monkeypatch: pytest.MonkeyPatch) -> LlamaBenchCapabilities:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    clear_bench_capability_cache()
    return inspect_llama_bench(fake_bench, include_probe_output=True, use_cache=False)


@pytest.fixture
def screen_plan_dir(tmp_path: Path, fake_binary: Path, fake_model: Path) -> Path:
    workload = Path(__file__).resolve().parents[2] / "workloads/smoke-test.jsonl"
    _, output = create_search_plan(
        goal=OptimizationGoal.BALANCED,
        output_dir=tmp_path / "screen-plan",
        binary=fake_binary,
        model=fake_model,
        workload=workload,
        maximum_profiles=8,
    )
    return output


@pytest.fixture
def evaluation_screening_dir(
    tmp_path: Path,
    screen_plan_dir: Path,
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    scenario_path = tmp_path / "screen-scenario.yaml"
    scenario_path.write_text(
        "schema_version: '1.0'\nscenarios:\n"
        "  - {id: decode, prompt_tokens: 0, generation_tokens: 16}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "healthy-jsonl")
    clear_bench_capability_cache()
    result = run_screening(
        ScreeningConfig(
            plan_dir=screen_plan_dir,
            bench_binary=fake_bench,
            output_dir=tmp_path / "screening",
            scenario_path=scenario_path,
            advance_count=4,
            repetitions=1,
            invocation_timeout_seconds=2.0,
            total_timeout_seconds=60.0,
            sample_interval_seconds=0.05,
            allow_synthetic=True,
        )
    )
    assert result.exit_code == 0
    return result.output_dir


@pytest.fixture
def server_capabilities(fake_binary: Path) -> ServerCapabilities:
    clear_capability_cache()
    return inspect_llama_server_capabilities(
        fake_binary,
        include_probe_output=True,
        use_cache=False,
    )


@pytest.fixture
def config_factory(fake_binary: Path, fake_model: Path) -> Callable[..., LlamaServerConfig]:
    def factory(**updates: object) -> LlamaServerConfig:
        values: dict[str, object] = {
            "binary_path": fake_binary,
            "model_path": fake_model,
            "startup_timeout_seconds": 2.0,
            "request_timeout_seconds": 0.5,
            "shutdown_timeout_seconds": 0.5,
        }
        values.update(updates)
        return LlamaServerConfig.model_validate(values)

    return factory
