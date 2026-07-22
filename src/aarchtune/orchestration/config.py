"""Canonical orchestration configuration and input fingerprints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aarchtune.baseline.runner import hash_file_streaming
from aarchtune.hardware.detector import detect_hardware
from aarchtune.optimization.compatibility import hardware_fingerprint
from aarchtune.orchestration.models import OptimizeConfig


def configuration_hash(config: OptimizeConfig) -> str:
    value = config.model_dump(mode="json", exclude={"overwrite", "resume"})
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def input_fingerprint(config: OptimizeConfig) -> dict[str, str]:
    hardware = hardware_fingerprint(detect_hardware(model_path=config.model))
    return {
        "server_binary_sha256": hash_file_streaming(config.server_binary.resolve()),
        "bench_binary_sha256": hash_file_streaming(config.bench_binary.resolve()),
        "model_sha256": hash_file_streaming(config.model.resolve()),
        "workload_sha256": hash_file_streaming(config.workload.resolve()),
        "hardware_fingerprint": hardware.fingerprint_hash,
        "search_space_sha256": _optional_hash(config.search_space),
        "screening_scenarios_sha256": _optional_hash(config.screening_scenarios),
        "quality_policy_sha256": _optional_hash(config.quality_policy),
    }


def _optional_hash(path: Path | None) -> str:
    return "default" if path is None else hash_file_streaming(path.resolve())
