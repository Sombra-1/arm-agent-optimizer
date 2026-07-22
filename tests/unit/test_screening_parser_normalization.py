from __future__ import annotations

import json
from pathlib import Path

import pytest

from aarchtune.optimization.identity import stable_hash
from aarchtune.screening.errors import BenchParseError
from aarchtune.screening.models import (
    BenchSignature,
    BenchSignatureSettings,
    MetricKind,
    OutputFormat,
    ScreeningScenario,
)
from aarchtune.screening.normalization import normalize_record
from aarchtune.screening.parser import parse_bench_output


def _signature() -> BenchSignature:
    settings = BenchSignatureSettings(
        threads=8,
        threads_batch=12,
        batch_size=512,
        ubatch_size=128,
        mmap=True,
        numa_mode="disabled",
    )
    return BenchSignature(
        id="bench-t8-tb12-b512-u128-mmap",
        signature_hash=stable_hash(settings),
        settings=settings,
        compatible=True,
    )


def _row(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "n_prompt": 64,
        "n_gen": 16,
        "n_threads": 8,
        "n_threads_batch": 12,
        "n_batch": 512,
        "n_ubatch": 128,
        "avg_ts": 123.5,
        "stddev_ts": 1.2,
        "test_time_seconds": 0.4,
        "backend": "cpu",
        "unknown_future_field": {"preserved": True},
    }
    value.update(updates)
    return value


@pytest.mark.parametrize("output_format", list(OutputFormat))
def test_valid_machine_readable_formats(tmp_path: Path, output_format: OutputFormat) -> None:
    path = tmp_path / f"output.{output_format.value}"
    row = _row()
    if output_format is OutputFormat.JSONL:
        path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    elif output_format is OutputFormat.JSON:
        path.write_text(json.dumps({"results": [row, row]}))
    else:
        headers = list(row)
        values = [
            json.dumps(row[name]) if isinstance(row[name], dict) else str(row[name])
            for name in headers
        ]
        path.write_text(",".join(headers) + "\n" + ",".join(values) + "\n")
    records = parse_bench_output(path, output_format, "invocation")
    assert records
    assert records[0].raw["unknown_future_field"] is not None


@pytest.mark.parametrize(
    ("output_format", "text"),
    [
        (OutputFormat.JSONL, "{broken\n"),
        (OutputFormat.JSON, "[]"),
        (OutputFormat.CSV, ""),
    ],
)
def test_invalid_or_empty_output_is_rejected(
    tmp_path: Path, output_format: OutputFormat, text: str
) -> None:
    path = tmp_path / "invalid"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BenchParseError):
        parse_bench_output(path, output_format, "inv")


def test_normalization_preserves_sources_and_combined_semantics(tmp_path: Path) -> None:
    path = tmp_path / "row.jsonl"
    path.write_text(json.dumps(_row()) + "\n")
    record = parse_bench_output(path, OutputFormat.JSONL, "inv")[0]
    measurement = normalize_record(
        record,
        _signature(),
        ScreeningScenario(id="mixed", prompt_tokens=64, generation_tokens=16),
    )
    assert measurement.provenance_valid is True
    assert measurement.metric_kind is MetricKind.COMBINED
    assert measurement.throughput_tokens_per_second.value == 123.5
    assert measurement.throughput_tokens_per_second.source_path == "$.avg_ts"
    assert measurement.backend.value == "cpu"


@pytest.mark.parametrize(
    ("updates", "reason_fragment"),
    [
        ({"avg_ts": -1}, "negative"),
        ({"avg_ts": True}, "not numeric"),
        ({"avg_ts": float("nan")}, "NaN"),
        ({"avg_ts": float("inf")}, "NaN"),
        ({"avg_ts": "fast"}, "strict numeric"),
    ],
)
def test_invalid_throughput_remains_unavailable(
    tmp_path: Path, updates: dict[str, object], reason_fragment: str
) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(_row(**updates)) + "\n")
    record = parse_bench_output(path, OutputFormat.JSONL, "inv")[0]
    value = normalize_record(
        record,
        _signature(),
        ScreeningScenario(id="mixed", prompt_tokens=64, generation_tokens=16),
    ).throughput_tokens_per_second
    assert value.available is False
    assert reason_fragment in (value.reason or "")


def test_missing_values_and_settings_mismatch_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "missing.jsonl"
    row = _row(n_threads=9)
    row.pop("avg_ts")
    path.write_text(json.dumps(row) + "\n")
    measurement = normalize_record(
        parse_bench_output(path, OutputFormat.JSONL, "inv")[0],
        _signature(),
        ScreeningScenario(id="mixed", prompt_tokens=64, generation_tokens=16),
    )
    assert measurement.throughput_tokens_per_second.available is False
    assert measurement.provenance_valid is False
    assert any("threads mismatch" in item for item in measurement.provenance_errors)


def test_prompt_and_decode_kinds_remain_separate(tmp_path: Path) -> None:
    path = tmp_path / "row.jsonl"
    path.write_text(json.dumps(_row(n_prompt=64, n_gen=0)) + "\n")
    record = parse_bench_output(path, OutputFormat.JSONL, "inv")[0]
    prefill = normalize_record(
        record,
        _signature(),
        ScreeningScenario(id="prefill", prompt_tokens=64, generation_tokens=0),
    )
    assert prefill.metric_kind is MetricKind.PREFILL
    path.write_text(json.dumps(_row(n_prompt=0, n_gen=16)) + "\n")
    decode = normalize_record(
        parse_bench_output(path, OutputFormat.JSONL, "inv2")[0],
        _signature(),
        ScreeningScenario(id="decode", prompt_tokens=0, generation_tokens=16),
    )
    assert decode.metric_kind is MetricKind.DECODE
