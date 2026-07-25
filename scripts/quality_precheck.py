#!/usr/bin/env python3
"""Sanitize one baseline-only model quality precheck without retaining responses."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from aarchtune.baseline.models import BaselineManifest, BaselineSummary
from aarchtune.evaluation.quality_policy import load_quality_policy
from aarchtune.orchestration.stages import validate_baseline

EXPECTED_TASKS = 5
EXPECTED_REPETITIONS = 2
EXPECTED_ATTEMPTS = EXPECTED_TASKS * EXPECTED_REPETITIONS

HISTORICAL_QUALITY_REFERENCES = (
    {
        "model_label": "Qwen2.5-1.5B-Instruct Q4_K_M",
        "output_contract": "prompt_only",
        "source": "separate earlier native run",
        "request_success_rate": 1.0,
        "task_success_rate": 0.0,
        "json_validity_rate": 0.3,
        "validator_pass_rate": 0.48,
    },
    {
        "model_label": "Qwen2.5-7B-Instruct Q3_K_M",
        "output_contract": "prompt_only",
        "source": "separate earlier native run",
        "request_success_rate": 1.0,
        "task_success_rate": 0.4,
        "json_validity_rate": 0.8,
        "validator_pass_rate": 0.72,
    },
    {
        "model_label": "Qwen2.5-14B-Instruct Q3_K_M",
        "output_contract": "prompt_only",
        "source": "separate earlier native run",
        "request_success_rate": 1.0,
        "task_success_rate": 0.4,
        "json_validity_rate": 0.8,
        "validator_pass_rate": 0.72,
    },
    {
        "model_label": "Qwen2.5-7B-Instruct Q3_K_M",
        "output_contract": "json_object",
        "source": "separate earlier native run",
        "request_success_rate": 1.0,
        "task_success_rate": 0.4,
        "json_validity_rate": 0.8,
        "validator_pass_rate": 0.72,
    },
)


@dataclass(frozen=True)
class LicenseEvidence:
    repository: str
    revision: str
    path: str
    sha256: str
    license_id: str
    license_name: str

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.path}"


@dataclass(frozen=True)
class SourceModelProvenance:
    repository: str
    revision: str
    model_name: str


@dataclass(frozen=True)
class ModelProfile:
    repository: str
    revision: str
    filename: str
    quantization: str
    size_bytes: int
    sha256: str
    license_evidence: LicenseEvidence
    source_model: SourceModelProvenance
    parameter_class: str
    model_label: str
    architecture: str
    tokenizer_model: str
    chat_template_marker: str
    requires_initial_system_support: bool
    declared_source_repository: str = ""

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{self.filename}"

    @property
    def license(self) -> str:
        return self.license_evidence.license_name

    @property
    def license_id(self) -> str:
        return self.license_evidence.license_id


MODEL_PROFILES = {
    "qwen2.5-7b-q3-k-m": ModelProfile(
        repository="Qwen/Qwen2.5-7B-Instruct-GGUF",
        revision="293ca9a10157b0e5fc5cb32af8b636a88bede891",
        filename="qwen2.5-7b-instruct-q3_k_m.gguf",
        quantization="Q3_K_M",
        size_bytes=3_808_391_072,
        sha256="a96b16179dc6cc9afdf0cf7a96a80c199cbd00b9be207c3465be21cb721cca5e",
        license_evidence=LicenseEvidence(
            repository="Qwen/Qwen2.5-7B-Instruct-GGUF",
            revision="74ef91efd0899612867d6bb080ce5a2788ef6aa1",
            path="README.md",
            sha256="b813de546b5f0a1d8c49307d428582fd649be627d9b33a60ecf807a3381af05e",
            license_id="apache-2.0",
            license_name="Apache-2.0",
        ),
        source_model=SourceModelProvenance(
            repository="Qwen/Qwen2.5-7B-Instruct",
            revision="a09a35458c702b33eeacc393d103063234e8bc28",
            model_name="Qwen2.5-7B-Instruct",
        ),
        parameter_class="7B",
        model_label="Qwen2.5-7B-Instruct Q3_K_M",
        architecture="qwen2",
        tokenizer_model="gpt2",
        chat_template_marker="<|im_start|>",
        requires_initial_system_support=False,
    ),
    "qwen2.5-14b-q3-k-m": ModelProfile(
        repository="bartowski/Qwen2.5-14B-Instruct-GGUF",
        revision="05244aa5d871c661c80082a15d3bce44714d068d",
        filename="Qwen2.5-14B-Instruct-Q3_K_M.gguf",
        quantization="Q3_K_M",
        size_bytes=7_339_204_736,
        sha256="2f68ac3ba018f7de7641229f19adafde5e59d02bbf5651fdbcc510bb9f3facca",
        license_evidence=LicenseEvidence(
            repository="bartowski/Qwen2.5-14B-Instruct-GGUF",
            revision="05244aa5d871c661c80082a15d3bce44714d068d",
            path="README.md",
            sha256="70cafd53867968a19d1af97f91eb1e282306b4a004438e8fde3600525a4e55d8",
            license_id="apache-2.0",
            license_name="Apache-2.0",
        ),
        source_model=SourceModelProvenance(
            repository="Qwen/Qwen2.5-14B-Instruct",
            revision="cf98f3b3bbb457ad9e2bb7baf9a0125b6b88caa8",
            model_name="Qwen2.5-14B-Instruct",
        ),
        parameter_class="14B",
        model_label="Qwen2.5-14B-Instruct Q3_K_M",
        architecture="qwen2",
        tokenizer_model="gpt2",
        chat_template_marker="<|im_start|>",
        requires_initial_system_support=False,
    ),
    "mistral-nemo-12b-q4-k-m": ModelProfile(
        repository="bartowski/Mistral-Nemo-Instruct-2407-GGUF",
        revision="a2dd64a0a76ea1bdb2bb6ab6fa5496b003c7c908",
        filename="Mistral-Nemo-Instruct-2407-Q4_K_M.gguf",
        quantization="Q4_K_M",
        size_bytes=7_477_208_192,
        sha256="7c1a10d202d8788dbe5628dc962254d10654c853cae6aaeca0618f05490d4a46",
        license_evidence=LicenseEvidence(
            repository="bartowski/Mistral-Nemo-Instruct-2407-GGUF",
            revision="a2dd64a0a76ea1bdb2bb6ab6fa5496b003c7c908",
            path="README.md",
            sha256="987b63374b1441d14b35efa83705bd6732768f53f8e5b731f818d7181e1f5b2e",
            license_id="apache-2.0",
            license_name="Apache-2.0",
        ),
        source_model=SourceModelProvenance(
            repository="mistralai/Mistral-Nemo-Instruct-2407",
            revision="04d8a90549d23fc6bd7f642064003592df51e9b3",
            model_name="Mistral-Nemo-Instruct-2407",
        ),
        parameter_class="12B",
        model_label="Mistral-Nemo-Instruct-2407 12B Q4_K_M",
        architecture="llama",
        tokenizer_model="gpt2",
        chat_template_marker="[INST]",
        requires_initial_system_support=True,
        declared_source_repository="mistralai/Mistral-Nemo-Instruct-2407",
    ),
}

OUTPUT_CONTRACTS = {
    "prompt_only": {
        "response_format_mode": "none",
        "response_format_applied": False,
        "response_format_type": None,
    },
    "json_object": {
        "response_format_mode": "json_object",
        "response_format_applied": True,
        "response_format_type": "json_object",
    },
}

_CLASSIFICATION_ORDER = (
    "request_failed",
    "timeout",
    "valid_json",
    "malformed_json",
    "missing_required_field",
    "wrong_schema",
    "wrong_allowed_value",
    "forbidden_text_present",
)

_ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def validate_environment(environment: dict[str, str]) -> dict[str, str]:
    """Validate records before writing them to the GitHub Actions environment."""

    for key, value in environment.items():
        if not _ENVIRONMENT_KEY.fullmatch(key):
            raise ValueError(f"workflow environment key is invalid: {key}")
        if "\n" in value or "\r" in value:
            raise ValueError(f"workflow environment value is multiline: {key}")
    return environment


def get_model_profile(name: str) -> ModelProfile:
    """Resolve one immutable allowlisted model profile."""

    try:
        profile = MODEL_PROFILES[name]
    except KeyError as error:
        raise ValueError(f"model profile is not allowlisted: {name}") from error
    full_sha = re.compile(r"^[0-9a-f]{40}$")
    sha256 = re.compile(r"^[0-9a-f]{64}$")
    if not full_sha.fullmatch(profile.revision):
        raise ValueError(f"model profile revision is not immutable: {name}")
    evidence = profile.license_evidence
    if not full_sha.fullmatch(evidence.revision):
        raise ValueError(f"license evidence revision is not immutable: {name}")
    if not evidence.path.strip():
        raise ValueError(f"license evidence path is empty: {name}")
    if not sha256.fullmatch(evidence.sha256):
        raise ValueError(f"license evidence SHA-256 is malformed: {name}")
    if not evidence.license_id.strip():
        raise ValueError(f"license evidence ID is empty: {name}")
    if not evidence.repository.strip() or not evidence.license_name.strip():
        raise ValueError(f"license evidence provenance is incomplete: {name}")
    source = profile.source_model
    if not source.repository.strip() or not source.model_name.strip():
        raise ValueError(f"source model provenance is incomplete: {name}")
    if not full_sha.fullmatch(source.revision):
        raise ValueError(f"source model revision is not immutable: {name}")
    if profile.size_bytes <= 0 or not sha256.fullmatch(profile.sha256):
        raise ValueError(f"model artifact identity is invalid: {name}")
    if not all(
        (
            profile.model_label.strip(),
            profile.parameter_class.strip(),
            profile.architecture.strip(),
            profile.tokenizer_model.strip(),
            profile.chat_template_marker.strip(),
        )
    ):
        raise ValueError(f"model profile provenance is incomplete: {name}")
    return profile


def profile_environment(name: str) -> dict[str, str]:
    """Return the fixed workflow environment for one allowlisted profile."""

    profile = get_model_profile(name)
    return validate_environment(
        {
            "MODEL_REPOSITORY": profile.repository,
            "MODEL_REVISION": profile.revision,
            "MODEL_FILENAME": profile.filename,
            "MODEL_QUANTIZATION": profile.quantization,
            "MODEL_LICENSE": profile.license,
            "MODEL_LICENSE_ID": profile.license_id,
            "MODEL_LABEL": profile.model_label,
            "MODEL_ARCHITECTURE": profile.architecture,
            "MODEL_TOKENIZER": profile.tokenizer_model,
            "MODEL_CHAT_TEMPLATE_MARKER": profile.chat_template_marker,
            "MODEL_REQUIRES_INITIAL_SYSTEM": str(profile.requires_initial_system_support).lower(),
            "GGUF_DECLARED_SOURCE_REPOSITORY": profile.declared_source_repository,
            "SOURCE_MODEL_REPOSITORY": profile.source_model.repository,
            "SOURCE_MODEL_REVISION": profile.source_model.revision,
            "SOURCE_MODEL_NAME": profile.source_model.model_name,
            "LICENSE_REPOSITORY": profile.license_evidence.repository,
            "LICENSE_REVISION": profile.license_evidence.revision,
            "LICENSE_PATH": profile.license_evidence.path,
            "LICENSE_EVIDENCE_SHA256": profile.license_evidence.sha256,
            "LICENSE_EVIDENCE_URL": profile.license_evidence.url,
            "MODEL_PARAMETER_CLASS": profile.parameter_class,
            "MODEL_SIZE_BYTES": str(profile.size_bytes),
            "MODEL_SHA256": profile.sha256,
            "MODEL_URL": profile.url,
        }
    )


def output_contract_environment(name: str) -> dict[str, str]:
    """Return the fixed request-provenance mapping for one allowlisted contract."""

    try:
        contract = OUTPUT_CONTRACTS[name]
    except KeyError as error:
        raise ValueError(f"output contract is not allowlisted: {name}") from error
    return validate_environment(
        {
            "OUTPUT_CONTRACT": name,
            "RESPONSE_FORMAT_MODE": str(contract["response_format_mode"]),
        }
    )


def classify_probe_result(exit_code: int, stderr: str) -> tuple[str, str]:
    """Classify a bounded model-load probe without preserving its output."""

    lowered = stderr.lower()
    resource_markers = (
        "out of memory",
        "cannot allocate memory",
        "std::bad_alloc",
        "memory allocation failed",
        "killed",
    )
    if exit_code == 0:
        return "model_loaded", "quality_evidence_incomplete"
    if exit_code == 124:
        return "probe_timeout", "quality_evidence_incomplete"
    if exit_code in {9, 137} or any(marker in lowered for marker in resource_markers):
        return "resource_incompatible", "resource_incompatible"
    return "model_probe_failed", "model_runtime_incompatible"


def verify_model(path: Path, expected_size: int, expected_sha256: str) -> dict[str, Any]:
    """Verify an immutable model blob before it can be used for inference."""

    if not path.is_file():
        raise ValueError(f"model file does not exist: {path}")
    size = path.stat().st_size
    if size != expected_size:
        raise ValueError(f"model size mismatch: expected {expected_size}, observed {size}")
    hasher = hashlib.sha256()
    with path.open("rb") as model:
        for chunk in iter(lambda: model.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"model SHA-256 mismatch: expected {expected_sha256}, observed {digest}")
    return {"size_bytes": size, "sha256": digest, "verified": True}


def verify_license(path: Path, evidence: LicenseEvidence) -> dict[str, Any]:
    """Verify an immutable repository license file without publishing its contents."""

    if not re.fullmatch(r"[0-9a-f]{40}", evidence.revision):
        raise ValueError("license evidence revision is not immutable")
    if not path.is_file():
        raise ValueError(f"license evidence file does not exist: {path}")
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != evidence.sha256:
        raise ValueError(
            f"license evidence SHA-256 mismatch: expected {evidence.sha256}, observed {digest}"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("license evidence is not valid UTF-8") from error
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("license evidence YAML front matter is missing")
    try:
        closing_index = lines[1:].index("---") + 1
    except ValueError as error:
        raise ValueError("license evidence YAML front matter is missing") from error
    try:
        front_matter = yaml.safe_load("\n".join(lines[1:closing_index]))
    except yaml.YAMLError as error:
        raise ValueError("license evidence YAML front matter is malformed") from error
    if not isinstance(front_matter, dict) or "license" not in front_matter:
        raise ValueError("license field is missing from evidence")
    observed = front_matter["license"]
    if not isinstance(observed, str) or not observed.strip():
        raise ValueError("license field is null or empty")
    if observed != evidence.license_id:
        raise ValueError(
            f"license ID mismatch: expected {evidence.license_id}, observed {observed}"
        )
    return {
        "license_name": evidence.license_name,
        "license_id": evidence.license_id,
        "license_repository": evidence.repository,
        "license_revision": evidence.revision,
        "license_path": evidence.path,
        "license_evidence_sha256": digest,
        "license_verified": True,
    }


_GGUF_SCALAR_FORMATS = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "f",
    7: "?",
    10: "Q",
    11: "q",
    12: "d",
}


def _read_struct(handle: BinaryIO, format_code: str) -> Any:
    size = struct.calcsize(f"<{format_code}")
    payload = handle.read(size)
    if len(payload) != size:
        raise ValueError("GGUF metadata is truncated")
    return struct.unpack(f"<{format_code}", payload)[0]


def _read_gguf_string(handle: BinaryIO) -> str:
    size = _read_struct(handle, "Q")
    payload = handle.read(size)
    if len(payload) != size:
        raise ValueError("GGUF metadata string is truncated")
    return payload.decode("utf-8", errors="strict")


def _read_gguf_value(handle: BinaryIO, value_type: int, *, retain: bool = True) -> Any:
    if value_type in _GGUF_SCALAR_FORMATS:
        value = _read_struct(handle, _GGUF_SCALAR_FORMATS[value_type])
        return value if retain else None
    if value_type == 8:
        value = _read_gguf_string(handle)
        return value if retain else None
    if value_type == 9:
        subtype = _read_struct(handle, "I")
        count = _read_struct(handle, "Q")
        if subtype in _GGUF_SCALAR_FORMATS:
            handle.seek(struct.calcsize(f"<{_GGUF_SCALAR_FORMATS[subtype]}") * count, 1)
        else:
            for _ in range(count):
                _read_gguf_value(handle, subtype, retain=False)
        return None
    raise ValueError(f"unsupported GGUF metadata value type: {value_type}")


def verify_gguf_metadata(path: Path, profile: ModelProfile) -> dict[str, Any]:
    """Verify required model metadata without retaining the embedded template."""

    if not path.is_file():
        raise ValueError(f"model file does not exist: {path}")
    wanted = {
        "general.architecture",
        "general.name",
        "general.base_model.0.name",
        "general.base_model.0.repo_url",
        "tokenizer.ggml.model",
        "tokenizer.chat_template",
    }
    observed: dict[str, Any] = {}
    with path.open("rb") as model:
        if model.read(4) != b"GGUF":
            raise ValueError("model file is not GGUF")
        version = _read_struct(model, "I")
        if version not in {2, 3}:
            raise ValueError(f"unsupported GGUF version: {version}")
        _read_struct(model, "Q")
        metadata_count = _read_struct(model, "Q")
        for _ in range(metadata_count):
            key = _read_gguf_string(model)
            value_type = _read_struct(model, "I")
            value = _read_gguf_value(model, value_type, retain=key in wanted)
            if key in wanted:
                observed[key] = value
    architecture = observed.get("general.architecture")
    if architecture != profile.architecture:
        raise ValueError(
            f"GGUF architecture mismatch: expected {profile.architecture}, observed {architecture}"
        )
    tokenizer = observed.get("tokenizer.ggml.model")
    if tokenizer != profile.tokenizer_model:
        raise ValueError(
            f"GGUF tokenizer mismatch: expected {profile.tokenizer_model}, observed {tokenizer}"
        )
    template = observed.get("tokenizer.chat_template")
    if not isinstance(template, str) or profile.chat_template_marker not in template:
        raise ValueError("GGUF embedded chat template is missing or incompatible")
    initial_system_supported = (
        'messages[0]["role"] == "system"' in template
        or "messages[0]['role'] == 'system'" in template
    )
    if profile.requires_initial_system_support and not initial_system_supported:
        raise ValueError("GGUF chat template does not support an initial system message")
    return {
        "architecture": architecture,
        "tokenizer_model": tokenizer,
        "general_name": observed.get("general.name"),
        "embedded_base_model_name": observed.get("general.base_model.0.name"),
        "embedded_base_model_repository": observed.get("general.base_model.0.repo_url"),
        "native_chat_template_present": True,
        "chat_template_marker_verified": True,
        "initial_system_message_supported": initial_system_supported,
        "thinking_markers_present": "<think>" in template,
        "verified": True,
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _validator_results(record: dict[str, Any]) -> list[dict[str, Any]]:
    validation = record.get("validation")
    if not isinstance(validation, dict):
        return []
    results = validation.get("validator_results")
    return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []


def classify_attempt(record: dict[str, Any]) -> dict[str, Any]:
    """Return classifications and validator names without response content or observations."""

    execution = record.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    results = _validator_results(record)
    by_name = {
        item.get("validator"): item.get("passed")
        for item in results
        if isinstance(item.get("validator"), str)
    }
    classifications: set[str] = set()
    if execution.get("request_succeeded") is not True:
        classifications.add("request_failed")
    if execution.get("timed_out") is True:
        classifications.add("timeout")
    if by_name.get("valid_json") is True:
        classifications.add("valid_json")
    elif "valid_json" in by_name:
        classifications.add("malformed_json")
    if by_name.get("required_fields") is False:
        classifications.add("missing_required_field")
    if by_name.get("json_schema") is False:
        classifications.add("wrong_schema")
    if by_name.get("allowed_value") is False or by_name.get("exact_value") is False:
        classifications.add("wrong_allowed_value")
    if by_name.get("not_contains_text") is False:
        classifications.add("forbidden_text_present")
    failed_validators = sorted(
        name for name, passed in by_name.items() if passed is False and isinstance(name, str)
    )
    return {
        "attempt_id": record.get("attempt_id"),
        "repetition": record.get("repetition"),
        "classifications": [item for item in _CLASSIFICATION_ORDER if item in classifications],
        "failed_validators": failed_validators,
        "task_passed": (
            record.get("validation", {}).get("passed")
            if isinstance(record.get("validation"), dict)
            else None
        ),
    }


def _evidence_errors(
    baseline_dir: Path,
    summary: BaselineSummary,
    records: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    output_contract: str,
    minimum_fraction: float,
    minimum_repetitions: int,
) -> list[str]:
    errors: list[str] = []
    valid, reason = validate_baseline(baseline_dir)
    if not valid:
        errors.append(reason or "existing baseline validation failed")
    benchmark = summary.benchmark
    if benchmark.total_configured_attempts != EXPECTED_ATTEMPTS:
        errors.append("configured attempt count is not 10")
    if benchmark.measured_attempts_completed != EXPECTED_ATTEMPTS:
        errors.append("completed attempt count is not 10")
    fraction = (
        benchmark.measured_attempts_completed / benchmark.total_configured_attempts
        if benchmark.total_configured_attempts
        else 0.0
    )
    if fraction < minimum_fraction:
        errors.append("completed-attempt fraction is below the existing quality policy")
    if summary.repetitions < minimum_repetitions:
        errors.append("repetitions per task are below the existing quality policy")
    if len(records) != EXPECTED_ATTEMPTS:
        errors.append("raw validation record count is not 10")
    if len(measurements) != EXPECTED_ATTEMPTS:
        errors.append("request provenance record count is not 10")
    attempt_ids = [item.get("attempt_id") for item in records]
    if len(set(attempt_ids)) != len(attempt_ids):
        errors.append("attempt IDs are not unique")
    task_counts = Counter(item.get("task_id") for item in records)
    if len(task_counts) != EXPECTED_TASKS or any(
        count != EXPECTED_REPETITIONS for count in task_counts.values()
    ):
        errors.append("per-task repetition evidence is incomplete")
    expected_contract = OUTPUT_CONTRACTS[output_contract]
    execution = summary.execution
    if (
        execution.response_format_mode.value != expected_contract["response_format_mode"]
        or execution.response_format_applied is not expected_contract["response_format_applied"]
        or execution.response_format_type != expected_contract["response_format_type"]
    ):
        errors.append("baseline response-format provenance does not match the selected contract")
    for measurement in measurements:
        request = measurement.get("request")
        if not isinstance(request, dict):
            errors.append("measured request provenance is missing")
            break
        if (
            request.get("response_format_mode") != expected_contract["response_format_mode"]
            or request.get("response_format_applied")
            is not expected_contract["response_format_applied"]
            or request.get("response_format_type") != expected_contract["response_format_type"]
        ):
            errors.append("a measured request used the wrong response-format contract")
            break
    return errors


def sanitize_baseline(
    baseline_dir: Path,
    policy_path: Path,
    output_dir: Path,
    output_contract: str = "prompt_only",
) -> str:
    """Validate, classify, and write only response-free quality evidence."""

    output_dir.mkdir(parents=True, exist_ok=True)
    if output_contract not in OUTPUT_CONTRACTS:
        raise ValueError(f"output contract is not allowlisted: {output_contract}")
    policy_source = load_quality_policy(policy_path)
    summary = BaselineSummary.model_validate_json(
        (baseline_dir / "baseline-summary.json").read_text(encoding="utf-8")
    )
    manifest = BaselineManifest.model_validate_json(
        (baseline_dir / "manifest.json").read_text(encoding="utf-8")
    )
    records = [
        json.loads(line)
        for line in (baseline_dir / "raw-attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("raw validation evidence must contain JSON objects")
    measurements = [
        json.loads(line)
        for line in (baseline_dir / "request-metrics.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    if not all(isinstance(measurement, dict) for measurement in measurements):
        raise ValueError("request provenance evidence must contain JSON objects")

    policy = policy_source.policy
    evidence_errors = _evidence_errors(
        baseline_dir,
        summary,
        records,
        measurements,
        output_contract,
        policy.minimum_evidence.completed_attempt_fraction,
        policy.minimum_evidence.repetitions_per_task,
    )
    quality = summary.quality
    aggregate = {
        "output_contract": output_contract,
        "response_format_mode": summary.execution.response_format_mode.value,
        "response_format_applied": summary.execution.response_format_applied,
        "response_format_type": summary.execution.response_format_type,
        "configured_attempts": summary.benchmark.total_configured_attempts,
        "completed_attempts": summary.benchmark.measured_attempts_completed,
        "completed_attempt_fraction": (
            summary.benchmark.measured_attempts_completed
            / summary.benchmark.total_configured_attempts
            if summary.benchmark.total_configured_attempts
            else None
        ),
        "request_success_rate": quality.request_success_rate,
        "task_success_rate": quality.task_attempt_success_rate,
        "json_validity_rate": quality.json_validity_rate,
        "validator_pass_rate": quality.validator_pass_rate,
        "timeout_rate": quality.timeout_rate,
    }
    metric_failures: list[str] = []
    for name in (
        "request_success_rate",
        "task_success_rate",
        "json_validity_rate",
        "validator_pass_rate",
    ):
        value = aggregate[name]
        threshold = getattr(policy.absolute_minimums, name)
        if value is None or value < threshold:
            metric_failures.append(name)
    if quality.timeout_rate is None or quality.timeout_rate > policy.maximums.timeout_rate:
        metric_failures.append("timeout_rate")
    for validator_type in policy.critical_validator_types:
        statistics = quality.per_validator_type.get(validator_type)
        if statistics is None or statistics.failed:
            metric_failures.append(f"critical_validator:{validator_type.value}")

    if evidence_errors:
        outcome = "quality_evidence_incomplete"
    elif metric_failures:
        outcome = "quality_policy_failed"
    else:
        outcome = "quality_policy_passed"

    per_task: list[dict[str, Any]] = []
    for task_id in sorted({str(item.get("task_id")) for item in records}):
        attempts = [classify_attempt(item) for item in records if item.get("task_id") == task_id]
        classification_counts = {
            classification: sum(
                classification in attempt["classifications"] for attempt in attempts
            )
            for classification in _CLASSIFICATION_ORDER
        }
        per_task.append(
            {
                "task_id": task_id,
                "attempts_completed": len(attempts),
                "attempts_passed": sum(attempt["task_passed"] is True for attempt in attempts),
                "classifications": classification_counts,
                "attempts": attempts,
            }
        )

    sanitized_manifest = {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "status": manifest.status.value,
        "stage": manifest.stage.value,
        "completed_attempt_count": manifest.completed_attempt_count,
        "server_stopped": manifest.server_stopped,
        "sampler_stopped": manifest.sampler_stopped,
        "output_contract": output_contract,
        "response_format_mode": summary.execution.response_format_mode.value,
        "response_format_applied": summary.execution.response_format_applied,
        "response_format_type": summary.execution.response_format_type,
    }
    request_generation_provenance = {
        "measured_request_count": len(measurements),
        "temperature_values": sorted(
            {
                request.get("temperature")
                for measurement in measurements
                if isinstance((request := measurement.get("request")), dict)
            }
        ),
        "seed_values": sorted(
            {
                request.get("seed")
                for measurement in measurements
                if isinstance((request := measurement.get("request")), dict)
            }
        ),
        "stream_values": sorted(
            {
                request.get("stream")
                for measurement in measurements
                if isinstance((request := measurement.get("request")), dict)
            }
        ),
        "max_tokens_by_task": {
            task_id: sorted(
                {
                    request.get("max_tokens")
                    for measurement in measurements
                    if measurement.get("task_id") == task_id
                    and isinstance((request := measurement.get("request")), dict)
                }
            )
            for task_id in sorted(
                {
                    str(measurement.get("task_id"))
                    for measurement in measurements
                    if measurement.get("task_id") is not None
                }
            )
        },
        "response_format_applied_count": sum(
            request.get("response_format_applied") is True
            for measurement in measurements
            if isinstance((request := measurement.get("request")), dict)
        ),
        "response_format_type_values": sorted(
            {
                request.get("response_format_type")
                for measurement in measurements
                if isinstance((request := measurement.get("request")), dict)
                and request.get("response_format_type") is not None
            }
        ),
    }
    failed_validator_names = {
        str(item.get("validator"))
        for record in records
        for item in _validator_results(record)
        if item.get("passed") is False
    }
    interpretation = {
        "response_format_accepted": not evidence_errors
        and summary.execution.response_format_applied,
        "serialization_improved": (
            quality.json_validity_rate is not None and quality.json_validity_rate > 0.8
        ),
        "semantic_quality_improved": (
            (
                quality.task_attempt_success_rate is not None
                and quality.task_attempt_success_rate > 0.4
            )
            or (quality.validator_pass_rate is not None and quality.validator_pass_rate > 0.72)
        ),
        "malformed_json_disappeared": quality.json_validity_rate == 1.0,
        "semantic_exact_value_failures_remain": bool(
            {"exact_value", "allowed_value"} & failed_validator_names
        ),
        "schema_failures_remain": bool({"json_schema", "required_fields"} & failed_validator_names),
        "policy_passed": outcome == "quality_policy_passed",
    }
    policy_outcome = {
        "outcome": outcome,
        "policy_sha256": policy_source.sha256,
        "aggregate": aggregate,
        "evidence_errors": evidence_errors,
        "failed_policy_checks": sorted(set(metric_failures)),
        "interpretation": interpretation,
        "thresholds": {
            "absolute_minimums": policy.absolute_minimums.model_dump(mode="json"),
            "maximums": policy.maximums.model_dump(mode="json"),
            "minimum_evidence": policy.minimum_evidence.model_dump(mode="json"),
            "critical_validator_types": [item.value for item in policy.critical_validator_types],
        },
    }
    (output_dir / "baseline-manifest.json").write_text(
        json.dumps(sanitized_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "validated-quality.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "request-provenance.json").write_text(
        json.dumps(request_generation_provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "per-task-classifications.json").write_text(
        json.dumps(per_task, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "quality-policy-outcome.json").write_text(
        json.dumps(policy_outcome, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return outcome


def write_comparison_summary(
    profile_name: str,
    output_contract: str,
    quality_outcome_path: Path,
    output_path: Path,
    duration_seconds: int,
) -> None:
    """Write current metrics beside clearly separate historical references."""

    profile = get_model_profile(profile_name)
    if output_contract not in OUTPUT_CONTRACTS:
        raise ValueError(f"output contract is not allowlisted: {output_contract}")
    outcome = _load_json(quality_outcome_path)
    aggregate = outcome.get("aggregate")
    current = {
        "model_profile": profile_name,
        "model_label": profile.model_label,
        "parameter_class": profile.parameter_class,
        "quantization": profile.quantization,
        "output_contract": output_contract,
        "policy_result": outcome.get("outcome"),
        "duration_seconds": duration_seconds,
    }
    for metric in (
        "request_success_rate",
        "task_success_rate",
        "json_validity_rate",
        "validator_pass_rate",
        "timeout_rate",
        "completed_attempt_fraction",
    ):
        current[metric] = aggregate.get(metric) if isinstance(aggregate, dict) else None
    report = {
        "comparison_type": "historical_reference_only",
        "benchmark": False,
        "statistical_combination": False,
        "current": current,
        "historical_references": list(HISTORICAL_QUALITY_REFERENCES),
    }
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-model")
    verify.add_argument("--path", type=Path, required=True)
    verify.add_argument("--size", type=int, required=True)
    verify.add_argument("--sha256", required=True)
    verify_license_parser = subparsers.add_parser("verify-license")
    verify_license_parser.add_argument("--profile", required=True)
    verify_license_parser.add_argument("--path", type=Path, required=True)
    verify_metadata = subparsers.add_parser("verify-gguf-metadata")
    verify_metadata.add_argument("--profile", required=True)
    verify_metadata.add_argument("--path", type=Path, required=True)
    sanitize = subparsers.add_parser("sanitize")
    sanitize.add_argument("--baseline-dir", type=Path, required=True)
    sanitize.add_argument("--policy", type=Path, required=True)
    sanitize.add_argument("--output-dir", type=Path, required=True)
    sanitize.add_argument(
        "--output-contract",
        choices=tuple(OUTPUT_CONTRACTS),
        default="prompt_only",
    )
    resolve = subparsers.add_parser("resolve-profile")
    resolve.add_argument("--profile", required=True)
    contract = subparsers.add_parser("resolve-contract")
    contract.add_argument("--contract", required=True)
    probe = subparsers.add_parser("classify-probe")
    probe.add_argument("--exit-code", type=int, required=True)
    probe.add_argument("--stderr", type=Path, required=True)
    comparison = subparsers.add_parser("comparison")
    comparison.add_argument("--profile", required=True)
    comparison.add_argument("--output-contract", required=True)
    comparison.add_argument("--quality-outcome", type=Path, required=True)
    comparison.add_argument("--output", type=Path, required=True)
    comparison.add_argument("--duration-seconds", type=int, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "verify-model":
        print(json.dumps(verify_model(args.path, args.size, args.sha256), sort_keys=True))
        return 0
    if args.command == "verify-license":
        profile = get_model_profile(args.profile)
        print(json.dumps(verify_license(args.path, profile.license_evidence), sort_keys=True))
        return 0
    if args.command == "verify-gguf-metadata":
        profile = get_model_profile(args.profile)
        print(json.dumps(verify_gguf_metadata(args.path, profile), sort_keys=True))
        return 0
    if args.command == "resolve-profile":
        for key, value in profile_environment(args.profile).items():
            print(f"{key}={value}")
        return 0
    if args.command == "resolve-contract":
        for key, value in output_contract_environment(args.contract).items():
            print(f"{key}={value}")
        return 0
    if args.command == "classify-probe":
        classification, outcome = classify_probe_result(
            args.exit_code,
            args.stderr.read_text(encoding="utf-8", errors="replace"),
        )
        print(json.dumps({"classification": classification, "outcome": outcome}, sort_keys=True))
        return 0
    if args.command == "comparison":
        write_comparison_summary(
            args.profile,
            args.output_contract,
            args.quality_outcome,
            args.output,
            args.duration_seconds,
        )
        return 0
    outcome = sanitize_baseline(
        args.baseline_dir,
        args.policy,
        args.output_dir,
        args.output_contract,
    )
    print(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
