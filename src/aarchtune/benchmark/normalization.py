"""Conservative normalization of version-variable llama.cpp response metrics."""

from __future__ import annotations

import math
from typing import cast

from pydantic import JsonValue

from aarchtune.benchmark.models import (
    NormalizedServerMetrics,
    OptionalMetric,
    unavailable_metric,
)


def _lookup(data: object, path: tuple[str, ...]) -> object:
    current = data
    for token in path:
        if not isinstance(current, dict) or token not in current:
            return _MISSING
        current = current[token]
    return current


_MISSING = object()


def _numeric_metric(
    data: object,
    paths: tuple[tuple[str, ...], ...],
    *,
    integer: bool = False,
    scale: float = 1.0,
) -> OptionalMetric:
    for path in paths:
        raw = _lookup(data, path)
        if raw is _MISSING:
            continue
        source_path = ".".join(path)
        valid_type = isinstance(raw, int) and not isinstance(raw, bool)
        if not integer:
            valid_type = isinstance(raw, (int, float)) and not isinstance(raw, bool)
        if not valid_type:
            return unavailable_metric(f"Malformed non-numeric server field {source_path}")
        numeric = cast(int | float, raw)
        value = float(numeric) * scale
        if not math.isfinite(value) or value < 0:
            return unavailable_metric(
                f"Malformed negative or non-finite server field {source_path}"
            )
        normalized: int | float = int(numeric) if integer else value
        return OptionalMetric(
            value=normalized,
            available=True,
            source="server_reported",
            source_path=source_path,
        )
    return unavailable_metric(
        "Metric not exposed by this llama.cpp response: "
        + " or ".join(".".join(path) for path in paths)
    )


def _client_rate(tokens: OptionalMetric, duration_ns: int) -> OptionalMetric:
    if not tokens.available or tokens.value is None:
        return unavailable_metric("Token count unavailable for client-derived throughput")
    if duration_ns <= 0:
        return unavailable_metric("Request duration was not positive")
    return OptionalMetric(
        value=float(tokens.value) / (duration_ns / 1_000_000_000),
        available=True,
        source="client_derived",
        source_path="request.duration_ns",
    )


def normalize_server_metrics(raw_response: JsonValue, duration_ns: int) -> NormalizedServerMetrics:
    """Normalize optional values without turning malformed timing data into request failure."""

    prompt_tokens = _numeric_metric(
        raw_response,
        (("usage", "prompt_tokens"), ("timings", "prompt_n")),
        integer=True,
    )
    completion_tokens = _numeric_metric(
        raw_response,
        (("usage", "completion_tokens"), ("timings", "predicted_n")),
        integer=True,
    )
    total_tokens = _numeric_metric(raw_response, (("usage", "total_tokens"),), integer=True)
    prompt_seconds = _numeric_metric(raw_response, (("timings", "prompt_ms"),), scale=0.001)
    generation_seconds = _numeric_metric(raw_response, (("timings", "predicted_ms"),), scale=0.001)
    prompt_rate = _numeric_metric(raw_response, (("timings", "prompt_per_second"),))
    generation_rate = _numeric_metric(raw_response, (("timings", "predicted_per_second"),))
    raw_fields: dict[str, JsonValue] = {}
    if isinstance(raw_response, dict):
        for name in ("usage", "timings"):
            value = raw_response.get(name)
            if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
                raw_fields[name] = value
    return NormalizedServerMetrics(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_processing_seconds=prompt_seconds,
        generation_seconds=generation_seconds,
        server_prompt_tokens_per_second=prompt_rate,
        server_generation_tokens_per_second=generation_rate,
        client_prompt_tokens_per_second=_client_rate(prompt_tokens, duration_ns),
        client_completion_tokens_per_second=_client_rate(completion_tokens, duration_ns),
        time_to_first_token_seconds=unavailable_metric(
            "True TTFT is unavailable because the client uses non-streaming requests"
        ),
        raw_fields=raw_fields,
    )
