from __future__ import annotations

import math

import pytest

from aarchtune.benchmark.normalization import normalize_server_metrics


def test_standard_usage_and_llama_timings_are_normalized_with_provenance() -> None:
    result = normalize_server_metrics(
        {
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            "timings": {
                "prompt_n": 20,
                "prompt_ms": 10.0,
                "prompt_per_second": 2000.0,
                "predicted_n": 8,
                "predicted_ms": 16.0,
                "predicted_per_second": 500.0,
            },
        },
        1_000_000_000,
    )
    assert result.prompt_tokens.value == 20
    assert result.prompt_tokens.source_path == "usage.prompt_tokens"
    assert result.prompt_processing_seconds.value == 0.01
    assert result.server_generation_tokens_per_second.value == 500
    assert result.client_completion_tokens_per_second.value == 8


def test_timing_token_counts_are_fallbacks_when_usage_is_missing() -> None:
    result = normalize_server_metrics({"timings": {"prompt_n": 4, "predicted_n": 2}}, 2_000_000_000)
    assert result.prompt_tokens.value == 4
    assert result.prompt_tokens.source_path == "timings.prompt_n"
    assert result.total_tokens.available is False


def test_missing_optional_fields_remain_unavailable() -> None:
    result = normalize_server_metrics({}, 1)
    assert result.prompt_tokens.value is None
    assert result.server_prompt_tokens_per_second.available is False
    assert "not exposed" in (result.prompt_tokens.reason or "")


@pytest.mark.parametrize(
    "bad_value",
    ["20", True, -1, math.nan, math.inf],
)
def test_wrong_negative_and_nonfinite_values_are_unavailable(bad_value: object) -> None:
    result = normalize_server_metrics({"usage": {"prompt_tokens": bad_value}}, 1_000)
    assert result.prompt_tokens.available is False
    assert result.client_prompt_tokens_per_second.available is False


def test_partial_fields_do_not_hide_valid_fields() -> None:
    result = normalize_server_metrics(
        {"usage": {"prompt_tokens": 5}, "timings": {"predicted_ms": "bad"}},
        1_000_000_000,
    )
    assert result.prompt_tokens.available is True
    assert result.generation_seconds.available is False


def test_no_total_latency_to_ttft_derivation() -> None:
    result = normalize_server_metrics({"usage": {"prompt_tokens": 2}}, 99_000_000)
    assert result.time_to_first_token_seconds.available is False
    assert "non-streaming" in (result.time_to_first_token_seconds.reason or "")
