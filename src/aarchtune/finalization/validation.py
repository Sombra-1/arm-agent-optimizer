"""Offline cross-artifact validation for final bundles."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from aarchtune.finalization.artifacts import artifact_hashes, read_checksums
from aarchtune.finalization.models import (
    BundleManifest,
    BundleStatus,
    BundleValidationResult,
    DeploymentProfile,
    ReportData,
)
from aarchtune.finalization.passport import verify_passport

BASE_REQUIRED = {
    "bundle-manifest.json",
    "optimization-passport.json",
    "passport-verification.json",
    "pareto-frontier.json",
    "report-data.json",
    "report.html",
    "stage-references.json",
    "checksums.sha256",
    "README.txt",
}
DEPLOYMENT_REQUIRED = {
    "selected-profile.yaml",
    "selected-command.json",
    "run-optimized.sh",
    "reproduce-evaluation.sh",
}
FORBIDDEN = {
    "raw-attempts.jsonl",
    "request-metrics.jsonl",
    "process-samples.jsonl",
    "server.log",
}
SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*[^\s<]+"
)


def validate_bundle(path: Path, *, allow_pending: bool = False) -> BundleValidationResult:
    root = path.expanduser().resolve()
    errors: list[str] = []
    missing = sorted(name for name in BASE_REQUIRED if not (root / name).is_file())
    if missing:
        return BundleValidationResult(
            valid=False,
            bundle_id=None,
            errors=[f"Missing required artifact: {name}" for name in missing],
            warnings=[],
        )
    try:
        manifest = BundleManifest.model_validate_json(
            (root / "bundle-manifest.json").read_text(encoding="utf-8")
        )
        report_data = ReportData.model_validate_json(
            (root / "report-data.json").read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        return BundleValidationResult(
            valid=False,
            bundle_id=None,
            errors=[f"Bundle schema failure: {exc}"],
            warnings=[],
        )
    if manifest.status not in {BundleStatus.COMPLETED, BundleStatus.DIAGNOSTIC}:
        errors.append("Bundle manifest status is not final")
    if manifest.validation_status != "valid" and not allow_pending:
        errors.append("Bundle manifest is not marked valid")
    deployable = manifest.selected_profile_id is not None
    if deployable:
        errors.extend(
            f"Missing deployment artifact: {name}"
            for name in sorted(DEPLOYMENT_REQUIRED)
            if not (root / name).is_file()
        )
    else:
        errors.extend(
            f"Diagnostic bundle contains deployment artifact: {name}"
            for name in sorted(DEPLOYMENT_REQUIRED)
            if (root / name).exists()
        )
    errors.extend(
        f"Raw or forbidden artifact copied into bundle: {name}"
        for name in sorted(FORBIDDEN)
        if (root / name).exists()
    )
    try:
        recorded = read_checksums(root / "checksums.sha256")
        actual = artifact_hashes(root, exclusions={"checksums.sha256"})
        if recorded != actual:
            errors.append("checksums.sha256 does not match final bundle contents")
    except (OSError, ValueError) as exc:
        errors.append(f"Invalid checksums: {exc}")
    passport_result = verify_passport(root / "optimization-passport.json")
    if not passport_result.valid:
        errors.extend(passport_result.errors)
    if passport_result.passport_id != manifest.passport_id:
        errors.append("Passport ID differs from bundle manifest")
    if report_data.passport_id != manifest.passport_id:
        errors.append("Report data Passport ID differs from bundle manifest")
    report = (root / "report.html").read_text(encoding="utf-8")
    if report_data.evaluation_id not in report or report_data.passport_id not in report:
        errors.append("HTML report identifiers do not match report-data.json")
    if "http://" in report or "https://" in report:
        errors.append("HTML report contains an external URL")
    if report_data.synthetic and "SYNTHETIC TEST EVIDENCE" not in report:
        errors.append("Synthetic report banner is missing")
    if "specific to the recorded hardware" not in (root / "README.txt").read_text():
        errors.append("Hardware-specific bundle disclaimer is missing")
    if deployable and (root / "selected-profile.yaml").is_file():
        try:
            profile = DeploymentProfile.model_validate(
                yaml.safe_load((root / "selected-profile.yaml").read_text(encoding="utf-8"))
            )
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"Invalid deployment profile: {exc}")
        else:
            if profile.profile_id != manifest.selected_profile_id:
                errors.append("Selected profile ID differs from bundle manifest")
            command = json.loads((root / "selected-command.json").read_text())
            if command.get("arguments") != profile.generated_command:
                errors.append("Selected command differs from deployment profile")
        script = (root / "run-optimized.sh").read_text(encoding="utf-8")
        required_script_text = (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "args=(",
            '"${args[@]}"',
        )
        if any(value not in script for value in required_script_text):
            errors.append("Run script is missing required safe Bash structure")
        if "eval" in script or "0.0.0.0" in script:
            errors.append("Run script contains forbidden execution or bind behavior")
        if not (root / "run-optimized.sh").stat().st_mode & 0o100:
            errors.append("Run script is not executable")
    compose_status = root / "docker-compose-status.json"
    compose_file = root / "docker-compose.optimized.yaml"
    if compose_status.exists() == compose_file.exists():
        errors.append("Exactly one Compose status or Compose artifact must exist")
    for item in root.iterdir():
        if item.is_file() and SECRET_PATTERN.search(
            item.read_text(encoding="utf-8", errors="replace")
        ):
            errors.append(f"Possible secret-like assignment in bundle: {item.name}")
    if any(root.glob(".*.tmp")):
        errors.append("Temporary incomplete artifacts remain")
    manifest_names = set(manifest.artifacts)
    actual_names = {item.name for item in root.iterdir() if item.is_file()}
    if manifest_names != actual_names - {"checksums.sha256", "bundle-manifest.json"}:
        errors.append("Bundle manifest artifact list differs from directory contents")
    return BundleValidationResult(
        valid=not errors,
        bundle_id=manifest.bundle_id,
        errors=errors,
        warnings=(
            ["Synthetic test evidence — not Arm performance evidence"] if manifest.synthetic else []
        ),
    )
