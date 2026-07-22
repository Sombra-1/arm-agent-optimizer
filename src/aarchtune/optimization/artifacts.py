"""Atomic deterministic search-plan artifacts and offline integrity validation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from aarchtune.baseline.artifacts import (
    JsonlArtifactWriter,
    atomic_write_json,
    prepare_run_directory,
)
from aarchtune.optimization.errors import PlanArtifactError
from aarchtune.optimization.identity import plan_hash, profile_hash
from aarchtune.optimization.models import (
    BaselineReference,
    CandidateCompatibilityClass,
    CandidateProfile,
    PlanValidationResult,
    SearchPlan,
)

REQUIRED_PLAN_ARTIFACTS = (
    "search-plan.json",
    "search-plan-summary.json",
    "baseline-reference.json",
    "hardware-fingerprint.json",
    "runtime-fingerprint.json",
    "search-space.json",
    "candidates.jsonl",
    "excluded-possibilities.jsonl",
    "warnings.json",
    "profiles",
)
FORBIDDEN_BENCHMARK_ARTIFACTS = {
    "raw-attempts.jsonl",
    "request-metrics.jsonl",
    "process-samples.jsonl",
    "process-summary.json",
    "quality-summary.json",
    "baseline-summary.json",
    "server.log",
}


def atomic_write_yaml(path: Path, value: dict[str, Any]) -> None:
    payload = yaml.safe_dump(value, sort_keys=True, allow_unicode=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        raise PlanArtifactError(f"Could not atomically write profile {path}: {exc}") from exc


def write_plan_artifacts(plan: SearchPlan, output_dir: Path, *, overwrite: bool) -> Path:
    root = prepare_run_directory(output_dir, overwrite=overwrite)
    profiles = root / "profiles"
    profiles.mkdir()
    atomic_write_json(root / "search-plan.json", plan)
    atomic_write_json(root / "search-plan-summary.json", plan.summary)
    baseline_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "baseline": (
            plan.input.baseline.model_dump(mode="json") if plan.input.baseline is not None else None
        ),
    }
    atomic_write_json(root / "baseline-reference.json", baseline_payload)
    atomic_write_json(root / "hardware-fingerprint.json", plan.input.hardware)
    atomic_write_json(root / "runtime-fingerprint.json", plan.input.runtime)
    atomic_write_json(root / "search-space.json", plan.search_space)
    with JsonlArtifactWriter(root / "candidates.jsonl") as output:
        for candidate in plan.candidates:
            output.append(candidate)
    with JsonlArtifactWriter(root / "excluded-possibilities.jsonl") as output:
        for exclusion in plan.excluded_possibilities:
            output.append(exclusion)
    atomic_write_json(
        root / "warnings.json",
        {
            "schema_version": "1.0",
            "warnings": [warning.model_dump(mode="json") for warning in plan.warnings],
        },
    )
    for candidate in plan.candidates:
        atomic_write_yaml(profiles / f"{candidate.id}.yaml", candidate.model_dump(mode="json"))
    return root


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value: Any = json.loads(line)
            if not isinstance(value, dict):
                raise PlanArtifactError(f"{path.name} line {line_number} is not an object")
            records.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanArtifactError(f"Could not parse {path}: {exc}") from exc
    return records


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanArtifactError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanArtifactError(f"{path.name} is not an object")
    return value


def validate_plan_directory(path: Path) -> PlanValidationResult:
    root = path.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    missing = [name for name in REQUIRED_PLAN_ARTIFACTS if not (root / name).exists()]
    if missing:
        return PlanValidationResult(
            valid=False,
            plan_id=None,
            plan_hash_valid=False,
            profile_count=0,
            errors=[f"Missing required artifact: {name}" for name in missing],
            warnings=[],
        )
    forbidden = sorted(name for name in FORBIDDEN_BENCHMARK_ARTIFACTS if (root / name).exists())
    errors.extend(f"Forbidden benchmark artifact exists: {name}" for name in forbidden)
    try:
        raw: Any = json.loads((root / "search-plan.json").read_text(encoding="utf-8"))
        plan = SearchPlan.model_validate_json(json.dumps(raw))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return PlanValidationResult(
            valid=False,
            plan_id=None,
            plan_hash_valid=False,
            profile_count=0,
            errors=[f"Invalid search-plan.json: {exc}", *errors],
            warnings=[],
        )
    computed_plan_hash = plan_hash(plan)
    hash_valid = computed_plan_hash == plan.plan_hash
    if not hash_valid:
        errors.append("Plan hash does not match canonical semantic content")
    ids = [candidate.id for candidate in plan.candidates]
    if len(ids) != len(set(ids)):
        errors.append("Candidate IDs are not unique")
    hashes = [candidate.profile_hash for candidate in plan.candidates]
    if len(hashes) != len(set(hashes)):
        errors.append("Equivalent candidate runtime configurations are duplicated")
    for candidate in plan.candidates:
        if profile_hash(candidate.runtime) != candidate.profile_hash:
            errors.append(f"Profile hash mismatch: {candidate.id}")
        if (
            candidate.executable
            and candidate.compatibility.classification is CandidateCompatibilityClass.INCOMPATIBLE
        ):
            errors.append(f"Incompatible candidate marked executable: {candidate.id}")
        if not candidate.executable or not candidate.compatibility.compatible:
            errors.append(f"Non-executable candidate retained in executable plan: {candidate.id}")
    if len(plan.candidates) > plan.summary.maximum_profiles:
        errors.append("Candidate count exceeds configured maximum")
    if len(plan.candidates) != plan.summary.compatible_profiles:
        errors.append("Summary compatible profile count does not match plan")
    try:
        jsonl_candidates = [
            CandidateProfile.model_validate_json(json.dumps(item))
            for item in _jsonl(root / "candidates.jsonl")
        ]
    except (ValidationError, PlanArtifactError) as exc:
        errors.append(f"Invalid candidates.jsonl: {exc}")
        jsonl_candidates = []
    if [item.model_dump(mode="json") for item in jsonl_candidates] != [
        item.model_dump(mode="json") for item in plan.candidates
    ]:
        errors.append("candidates.jsonl does not exactly match search-plan.json")
    try:
        exclusions = _jsonl(root / "excluded-possibilities.jsonl")
        expected_exclusions = [item.model_dump(mode="json") for item in plan.excluded_possibilities]
        if exclusions != expected_exclusions:
            errors.append("excluded-possibilities.jsonl does not exactly match search-plan.json")
    except PlanArtifactError as exc:
        errors.append(f"Invalid excluded-possibilities.jsonl: {exc}")
    profiles_dir = root / "profiles"
    expected_files = {f"{candidate.id}.yaml" for candidate in plan.candidates}
    actual_files = {item.name for item in profiles_dir.glob("*.yaml")}
    if actual_files != expected_files:
        errors.append("Candidate YAML file set does not match the plan")
    for candidate in plan.candidates:
        profile_path = profiles_dir / f"{candidate.id}.yaml"
        if not profile_path.is_file():
            continue
        try:
            loaded = CandidateProfile.model_validate_json(
                json.dumps(yaml.safe_load(profile_path.read_text(encoding="utf-8")))
            )
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"Invalid candidate YAML {profile_path.name}: {exc}")
            continue
        if loaded.model_dump(mode="json") != candidate.model_dump(mode="json"):
            errors.append(f"Candidate YAML does not match plan: {candidate.id}")
    try:
        baseline_payload: Any = json.loads(
            (root / "baseline-reference.json").read_text(encoding="utf-8")
        )
        recorded = baseline_payload.get("baseline") if isinstance(baseline_payload, dict) else None
        expected = (
            plan.input.baseline.model_dump(mode="json") if plan.input.baseline is not None else None
        )
        if recorded != expected:
            errors.append("baseline-reference.json does not match plan provenance")
        if recorded is not None:
            reference = BaselineReference.model_validate_json(json.dumps(recorded))
            manifest_path = reference.path / "manifest.json"
            try:
                current_manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            except OSError as exc:
                errors.append(f"Cannot verify referenced baseline manifest: {exc}")
            else:
                if current_manifest_hash != reference.manifest_sha256:
                    errors.append("Referenced baseline manifest hash does not match the plan")
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        errors.append(f"Invalid baseline-reference.json: {exc}")
    companion_artifacts: tuple[tuple[str, dict[str, Any]], ...] = (
        ("search-plan-summary.json", plan.summary.model_dump(mode="json")),
        ("hardware-fingerprint.json", plan.input.hardware.model_dump(mode="json")),
        ("runtime-fingerprint.json", plan.input.runtime.model_dump(mode="json")),
        ("search-space.json", plan.search_space.model_dump(mode="json")),
        (
            "warnings.json",
            {
                "schema_version": "1.0",
                "warnings": [item.model_dump(mode="json") for item in plan.warnings],
            },
        ),
    )
    for name, expected in companion_artifacts:
        try:
            if _json_object(root / name) != expected:
                errors.append(f"{name} does not exactly match search-plan.json")
        except PlanArtifactError as exc:
            errors.append(f"Invalid {name}: {exc}")
    if plan.summary.synthetic_fixture:
        warnings.append("Synthetic planning fixture — not Arm performance evidence")
    return PlanValidationResult(
        valid=not errors,
        plan_id=plan.plan_id,
        plan_hash_valid=hash_valid,
        profile_count=len(plan.candidates),
        errors=errors,
        warnings=warnings,
    )
