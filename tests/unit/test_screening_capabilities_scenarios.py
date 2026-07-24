from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from aarchtune.screening.capabilities import (
    clear_bench_capability_cache,
    inspect_llama_bench,
    parse_option_tokens,
    resolve_llama_bench_binary,
)
from aarchtune.screening.errors import BenchCapabilityError, BenchDiscoveryError, ScenarioError
from aarchtune.screening.models import (
    BooleanOptionForm,
    LlamaBenchCapabilities,
    OutputFormat,
)
from aarchtune.screening.scenarios import load_scenarios


def test_explicit_environment_and_path_discovery(
    tmp_path: Path, fake_bench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert resolve_llama_bench_binary(fake_bench) == fake_bench
    monkeypatch.setenv("AARCHTUNE_LLAMA_BENCH", str(fake_bench))
    assert resolve_llama_bench_binary() == fake_bench
    monkeypatch.delenv("AARCHTUNE_LLAMA_BENCH")
    path_entry = tmp_path / "llama-bench"
    path_entry.symlink_to(fake_bench)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert resolve_llama_bench_binary() == fake_bench.resolve()


def test_missing_and_nonexecutable_binary(tmp_path: Path) -> None:
    with pytest.raises(BenchDiscoveryError, match="not found"):
        resolve_llama_bench_binary(tmp_path / "missing")
    path = tmp_path / "llama-bench"
    path.write_text("fixture", encoding="utf-8")
    with pytest.raises(BenchDiscoveryError, match="not executable"):
        resolve_llama_bench_binary(path)


def test_version_help_formats_and_complete_tokens(
    bench_capabilities: LlamaBenchCapabilities,
) -> None:
    assert bench_capabilities.version_probe.successful
    assert bench_capabilities.help_probe.successful
    assert bench_capabilities.output.selected_format is OutputFormat.JSONL
    assert bench_capabilities.output.supported_formats == [
        OutputFormat.JSONL,
        OutputFormat.JSON,
        OutputFormat.CSV,
    ]
    assert bench_capabilities.mappings["threads"].selected_flag == "-t"
    assert {"-tb", "--threads-batch"} <= set(
        bench_capabilities.mappings["threads_batch"].aliases_observed
    )
    assert bench_capabilities.mappings["mmap"].boolean_form is BooleanOptionForm.NUMERIC_01
    assert bench_capabilities.mappings["mmap"].represents_boolean(True)
    assert bench_capabilities.mappings["mmap"].represents_boolean(False)


@pytest.mark.parametrize(
    ("mmap_help", "expected_form", "true_supported", "false_supported"),
    [
        ("--mmap <0|1>", BooleanOptionForm.NUMERIC_01, True, True),
        ("--mmap\n--no-mmap", BooleanOptionForm.PAIRED_SWITCHES, True, True),
        ("--mmap", BooleanOptionForm.TRUE_ONLY, True, False),
        ("", BooleanOptionForm.UNSUPPORTED, False, False),
    ],
)
def test_mmap_boolean_form_is_preserved_from_help(
    tmp_path: Path,
    mmap_help: str,
    expected_form: BooleanOptionForm,
    true_supported: bool,
    false_supported: bool,
) -> None:
    binary = tmp_path / "llama-bench"
    binary.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --version ]; then echo 'llama-bench test'; exit 0; fi\n"
        'if [ "$1" = --help ]; then\n'
        f"  printf '%s\\n' '-m, --model <path>' '-o, --output <jsonl>' '{mmap_help}'\n"
        "  exit 0\n"
        "fi\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)

    mapping = inspect_llama_bench(binary, use_cache=False).mappings["mmap"]

    assert mapping.boolean_form is expected_form
    assert mapping.represents_boolean(True) is true_supported
    assert mapping.represents_boolean(False) is false_supported


def test_fake_bench_requires_numeric_mmap_value(
    fake_bench: Path,
    fake_model: Path,
) -> None:
    base = [
        str(fake_bench),
        "--model",
        str(fake_model),
        "--generation-tokens",
        "1",
        "--output",
        "jsonl",
        "--mmap",
    ]
    disabled = subprocess.run([*base, "0"], capture_output=True, text=True, check=False)
    enabled = subprocess.run([*base, "1"], capture_output=True, text=True, check=False)
    missing = subprocess.run(base, capture_output=True, text=True, check=False)
    invalid = subprocess.run([*base, "2"], capture_output=True, text=True, check=False)

    assert disabled.returncode == 0
    assert '"mmap":false' in disabled.stdout
    assert enabled.returncode == 0
    assert '"mmap":true' in enabled.stdout
    assert missing.returncode != 0
    assert invalid.returncode != 0


def test_option_parser_prevents_substring_false_positives() -> None:
    tokens = parse_option_tokens("--threads-extra --threads=<n> text--model -tb, -t ")
    assert "--threads" in tokens
    assert "--threads-extra" in tokens
    assert "--model" not in tokens
    assert {"-tb", "-t"} <= tokens


def test_probe_timeouts_and_help_failure(fake_bench: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "version-timeout")
    timed = inspect_llama_bench(
        fake_bench,
        timeout_seconds=0.05,
        include_probe_output=True,
        use_cache=False,
    )
    assert timed.version_probe.timed_out is True
    assert timed.help_probe.successful is True
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", "help-failure")
    with pytest.raises(BenchCapabilityError, match="status 2"):
        inspect_llama_bench(fake_bench, use_cache=False)


def test_machine_readable_output_is_required(tmp_path: Path) -> None:
    binary = tmp_path / "llama-bench"
    binary.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --help ]; then echo '-m, --model FILE -t, --threads N'; "
        "else echo 'bench synthetic'; fi\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    with pytest.raises(BenchCapabilityError, match="machine-readable"):
        inspect_llama_bench(binary, use_cache=False)


def test_requested_output_format_and_cache_invalidation(tmp_path: Path, fake_bench: Path) -> None:
    copy = tmp_path / "llama-bench"
    copy.write_bytes(fake_bench.read_bytes())
    copy.chmod(0o755)
    clear_bench_capability_cache()
    first = inspect_llama_bench(copy, requested_format=OutputFormat.CSV)
    second = inspect_llama_bench(copy, requested_format=OutputFormat.CSV)
    assert first == second
    original_mtime = copy.stat().st_mtime_ns
    copy.write_bytes(copy.read_bytes() + b"\n")
    os.utime(copy, ns=(original_mtime + 1_000_000, original_mtime + 1_000_000))
    changed = inspect_llama_bench(copy, requested_format=OutputFormat.CSV)
    assert changed.binary_size == first.binary_size + 1


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("healthy-json", OutputFormat.JSON), ("healthy-csv", OutputFormat.CSV)],
)
def test_fixture_can_expose_single_machine_format(
    fake_bench: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected: OutputFormat,
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_BENCH_SCENARIO", scenario)
    inspected = inspect_llama_bench(fake_bench, use_cache=False)
    assert inspected.output.supported_formats == [expected]
    assert inspected.output.selected_format is expected


def test_default_and_custom_scenarios(
    tmp_path: Path, bench_capabilities: LlamaBenchCapabilities
) -> None:
    default = load_scenarios(None, bench_capabilities)
    assert [item.id for item in default.scenarios] == [
        "prefill-small",
        "prefill-medium",
        "decode",
        "mixed",
    ]
    custom = tmp_path / "scenarios.yaml"
    custom.write_text(
        "schema_version: '1.0'\nscenarios:\n"
        "  - {id: prompt, prompt_tokens: 64, generation_tokens: 0}\n",
        encoding="utf-8",
    )
    first = load_scenarios(custom, bench_capabilities)
    second = load_scenarios(custom, bench_capabilities)
    assert first.sha256 == second.sha256
    assert first.scenarios[0].id == "prompt"


@pytest.mark.parametrize(
    "content",
    [
        "schema_version: '1.0'\nunknown: true\nscenarios: []\n",
        "schema_version: '1.0'\nscenarios:\n"
        "  - {id: empty, prompt_tokens: 0, generation_tokens: 0}\n",
        "schema_version: '1.0'\nscenarios:\n"
        + "".join(
            f"  - {{id: s{i}, prompt_tokens: {i + 1}, generation_tokens: 0}}\n" for i in range(9)
        ),
    ],
)
def test_invalid_scenario_files_are_rejected(
    tmp_path: Path, bench_capabilities: LlamaBenchCapabilities, content: str
) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ScenarioError, match="Invalid"):
        load_scenarios(path, bench_capabilities)


def test_unsupported_scenarios_are_omitted_or_fail(
    bench_capabilities: LlamaBenchCapabilities,
) -> None:
    prompt_only = bench_capabilities.model_copy(
        update={
            "mappings": {
                **bench_capabilities.mappings,
                "generation_tokens": bench_capabilities.mappings["generation_tokens"].model_copy(
                    update={"supported": False, "selected_flag": None}
                ),
            }
        }
    )
    loaded = load_scenarios(None, prompt_only)
    assert {item.id for item in loaded.scenarios} == {"prefill-small", "prefill-medium"}
    neither = prompt_only.model_copy(
        update={
            "mappings": {
                **prompt_only.mappings,
                "prompt_tokens": prompt_only.mappings["prompt_tokens"].model_copy(
                    update={"supported": False, "selected_flag": None}
                ),
            }
        }
    )
    with pytest.raises(ScenarioError, match="No configured scenario"):
        load_scenarios(None, neither)

    generation_only = bench_capabilities.model_copy(
        update={
            "mappings": {
                **bench_capabilities.mappings,
                "prompt_tokens": bench_capabilities.mappings["prompt_tokens"].model_copy(
                    update={"supported": False, "selected_flag": None}
                ),
            }
        }
    )
    generated = load_scenarios(None, generation_only)
    assert [item.id for item in generated.scenarios] == ["decode"]
