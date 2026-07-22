"""Conservative canonical measurement normalization with source provenance."""

from __future__ import annotations

import math
import re
from typing import Any

from aarchtune.screening.models import (
    BenchSignature,
    CanonicalValue,
    NormalizedBenchMeasurement,
    RawBenchRecord,
    ScreeningScenario,
)

_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_FIELDS: dict[str, tuple[str, ...]] = {
    "prompt_tokens": ("prompt_tokens", "n_prompt", "pp"),
    "generation_tokens": ("generation_tokens", "n_gen", "tg"),
    "threads": ("threads", "n_threads"),
    "threads_batch": ("threads_batch", "n_threads_batch"),
    "batch_size": ("batch_size", "n_batch"),
    "ubatch_size": ("ubatch_size", "n_ubatch"),
    "throughput_tokens_per_second": ("tokens_per_second", "throughput", "avg_ts"),
    "throughput_standard_deviation": ("throughput_standard_deviation", "stddev_ts"),
    "test_time_seconds": ("test_time_seconds", "avg_time_seconds"),
    "model_size_bytes": ("model_size_bytes", "model_size"),
    "model_parameter_count": ("model_parameter_count", "model_n_params"),
    "backend": ("backend",),
    "build_commit": ("build_commit", "commit"),
    "build_number": ("build_number", "build"),
}
_INTEGER_FIELDS = {
    "prompt_tokens",
    "generation_tokens",
    "threads",
    "threads_batch",
    "batch_size",
    "ubatch_size",
    "model_size_bytes",
    "model_parameter_count",
    "build_number",
}
_TEXT_FIELDS = {"backend", "build_commit"}


def _unavailable(reason: str) -> CanonicalValue:
    return CanonicalValue(available=False, value=None, reason=reason)


def _canonical(raw: dict[str, Any], logical: str) -> CanonicalValue:
    key = next((candidate for candidate in _FIELDS[logical] if candidate in raw), None)
    if key is None:
        return _unavailable(f"No recognized source field for {logical}")
    value = raw[key]
    if logical in _TEXT_FIELDS:
        if not isinstance(value, str) or not value:
            return _unavailable(f"{key} is not a non-empty string")
        return CanonicalValue(available=True, value=value, source_path=f"$.{key}")
    if isinstance(value, str):
        if not _NUMBER.fullmatch(value.strip()):
            return _unavailable(f"{key} is not a strict numeric CSV value")
        value = (
            float(value) if any(character in value.lower() for character in ".e") else int(value)
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _unavailable(f"{key} is not numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        return _unavailable(f"{key} is NaN or infinite")
    if numeric < 0:
        return _unavailable(f"{key} is negative")
    if logical in _INTEGER_FIELDS:
        if not numeric.is_integer():
            return _unavailable(f"{key} is not an integer")
        normalized: int | float = int(numeric)
    else:
        normalized = numeric
    return CanonicalValue(available=True, value=normalized, source_path=f"$.{key}")


def normalize_record(
    record: RawBenchRecord,
    signature: BenchSignature,
    scenario: ScreeningScenario,
) -> NormalizedBenchMeasurement:
    values = {logical: _canonical(record.raw, logical) for logical in _FIELDS}
    errors: list[str] = []
    expected: dict[str, int | None] = {
        "prompt_tokens": scenario.prompt_tokens,
        "generation_tokens": scenario.generation_tokens,
        "threads": signature.settings.threads,
        "threads_batch": signature.settings.threads_batch,
        "batch_size": signature.settings.batch_size,
        "ubatch_size": signature.settings.ubatch_size,
    }
    for field, expected_value in expected.items():
        observed = values[field]
        if expected_value is not None and observed.available and observed.value != expected_value:
            errors.append(
                f"{field} mismatch: requested {expected_value}, output reported {observed.value}"
            )
    throughput = values["throughput_tokens_per_second"]
    if throughput.available and throughput.value == 0:
        errors.append("throughput is zero and is not a usable screening measurement")
    return NormalizedBenchMeasurement(
        measurement_id=f"{record.invocation_id}-row{record.row_index}",
        invocation_id=record.invocation_id,
        row_index=record.row_index,
        scenario_id=scenario.id,
        signature_id=signature.id,
        metric_kind=scenario.metric_kind,
        prompt_tokens=values["prompt_tokens"],
        generation_tokens=values["generation_tokens"],
        threads=values["threads"],
        threads_batch=values["threads_batch"],
        batch_size=values["batch_size"],
        ubatch_size=values["ubatch_size"],
        throughput_tokens_per_second=throughput,
        throughput_standard_deviation=values["throughput_standard_deviation"],
        test_time_seconds=values["test_time_seconds"],
        model_size_bytes=values["model_size_bytes"],
        model_parameter_count=values["model_parameter_count"],
        backend=values["backend"],
        build_commit=values["build_commit"],
        build_number=values["build_number"],
        provenance_valid=not errors,
        provenance_errors=errors,
    )
