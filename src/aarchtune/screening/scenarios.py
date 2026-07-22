"""Strict low-level scenario loading and capability filtering."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from aarchtune.screening.errors import ScenarioError
from aarchtune.screening.models import LlamaBenchCapabilities, ScenarioSet, ScenarioSource

DEFAULT_SCENARIOS = Path(__file__).resolve().parents[3] / "configs/default-screening-scenarios.yaml"
MAX_SCENARIO_BYTES = 128 * 1024


def load_scenarios(path: Path | None, capabilities: LlamaBenchCapabilities) -> ScenarioSource:
    source = (path or DEFAULT_SCENARIOS).expanduser().resolve()
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise ScenarioError(f"Cannot read scenario file {source}: {exc}") from exc
    if len(content) > MAX_SCENARIO_BYTES:
        raise ScenarioError(f"Scenario file exceeds {MAX_SCENARIO_BYTES} bytes")
    try:
        raw: Any = yaml.safe_load(content.decode("utf-8", errors="strict"))
        scenario_set = ScenarioSet.model_validate(raw)
    except (UnicodeDecodeError, yaml.YAMLError, ValidationError) as exc:
        raise ScenarioError(f"Invalid screening scenario configuration: {exc}") from exc
    prompt_supported = capabilities.mappings["prompt_tokens"].supported
    generation_supported = capabilities.mappings["generation_tokens"].supported
    supported = []
    omitted: list[dict[str, Any]] = []
    for scenario in scenario_set.scenarios:
        reasons = []
        if scenario.prompt_tokens and not prompt_supported:
            reasons.append("prompt-token flag unavailable")
        if scenario.generation_tokens and not generation_supported:
            reasons.append("generation-token flag unavailable")
        if reasons:
            omitted.append({"id": scenario.id, "reason": "; ".join(reasons)})
        else:
            supported.append(scenario)
    if not supported:
        raise ScenarioError("No configured scenario can be represented by this llama-bench")
    return ScenarioSource(
        path=source,
        sha256=hashlib.sha256(content).hexdigest(),
        scenarios=supported,
        omitted_scenarios=omitted,
    )
