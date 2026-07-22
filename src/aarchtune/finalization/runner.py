"""Validated finalization into a compact reproducible evidence bundle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from aarchtune.baseline.artifacts import atomic_write_json, prepare_run_directory
from aarchtune.evaluation.models import SelectionOutcome
from aarchtune.finalization.artifacts import (
    artifact_hashes,
    relative_path,
    write_bundle_readme,
    write_checksums,
    write_json,
    write_yaml,
)
from aarchtune.finalization.context import load_finalization_context
from aarchtune.finalization.deployment import (
    deployment_profile,
    selected_command,
    write_compose,
    write_reproduction_script,
    write_run_script,
)
from aarchtune.finalization.errors import BundleValidationError
from aarchtune.finalization.models import (
    BundleManifest,
    BundleStatus,
    FinalizeConfig,
    FinalizeRunResult,
)
from aarchtune.finalization.pareto import calculate_pareto_frontier
from aarchtune.finalization.passport import create_passport, verify_passport
from aarchtune.finalization.report import render_report
from aarchtune.finalization.report_data import create_report_data
from aarchtune.finalization.validation import validate_bundle


def finalize_evaluation(config: FinalizeConfig) -> FinalizeRunResult:
    context = load_finalization_context(
        config.evaluation_dir, allow_synthetic=config.allow_synthetic
    )
    root = prepare_run_directory(config.output_dir, overwrite=config.overwrite)
    created = datetime.now(UTC)
    bundle_id = f"bundle-{uuid.uuid4().hex[:12]}"
    deployable = context.selection.outcome in {
        SelectionOutcome.CANDIDATE_SELECTED,
        SelectionOutcome.BASELINE_RETAINED,
    }
    command = selected_command(context) if deployable else None
    pareto = calculate_pareto_frontier(
        context.summary.evaluation_id, context.results, context.decisions, context.selection
    )
    write_json(root / "pareto-frontier.json", pareto)
    passport = create_passport(context, root, pareto, command)
    write_json(root / "optimization-passport.json", passport)
    passport_verification = verify_passport(root / "optimization-passport.json")
    write_json(root / "passport-verification.json", passport_verification)
    profile = None
    if deployable:
        assert command is not None
        profile = deployment_profile(context, passport, command)
        write_yaml(root / "selected-profile.yaml", profile)
        write_json(
            root / "selected-command.json",
            {"schema_version": "1.0", "arguments": command},
        )
        write_run_script(root / "run-optimized.sh", profile, passport)
        write_reproduction_script(root / "reproduce-evaluation.sh", context)
    write_compose(root, config.container_image, profile)
    report_data = create_report_data(context, passport, pareto)
    write_json(root / "report-data.json", report_data)
    (root / "report.html").write_text(render_report(report_data), encoding="utf-8")
    stage_references = {
        "schema_version": "1.0",
        "references": [item.model_dump(mode="json") for item in passport.stage_artifact_hashes],
    }
    write_json(root / "stage-references.json", stage_references)
    write_bundle_readme(
        root / "README.txt",
        synthetic=context.summary.synthetic_fixture,
        deployable=deployable,
        outcome=context.selection.outcome.value,
    )
    status = BundleStatus.COMPLETED if deployable else BundleStatus.DIAGNOSTIC
    artifact_names = {
        item.name: item.name
        for item in root.iterdir()
        if item.is_file() and item.name not in {"bundle-manifest.json", "checksums.sha256"}
    }
    manifest = BundleManifest(
        bundle_id=bundle_id,
        created_at=created,
        status=status,
        evaluation_path=relative_path(context.root, root),
        evaluation_manifest_sha256=next(
            item.sha256
            for item in passport.stage_artifact_hashes
            if item.path.endswith("evaluation-manifest.json")
        ),
        passport_id=passport.passport_id,
        selection_outcome=context.selection.outcome.value,
        selected_profile_id=context.selection.selected_candidate_id if deployable else None,
        artifacts=artifact_names,
        artifact_hashes=artifact_hashes(
            root, exclusions={"bundle-manifest.json", "checksums.sha256"}
        ),
        validation_status="pending",
        synthetic=context.summary.synthetic_fixture,
    )
    atomic_write_json(root / "bundle-manifest.json", manifest)
    write_checksums(root)
    preliminary = validate_bundle(root, allow_pending=True)
    if not preliminary.valid:
        raise BundleValidationError("; ".join(preliminary.errors))
    manifest = manifest.model_copy(update={"validation_status": "valid"})
    atomic_write_json(root / "bundle-manifest.json", manifest)
    write_checksums(root)
    validation = validate_bundle(root)
    if not validation.valid:
        raise BundleValidationError("; ".join(validation.errors))
    exit_code: Literal[0, 1, 3, 4] = (
        0
        if deployable
        else (4 if context.selection.outcome is SelectionOutcome.NO_ELIGIBLE_CANDIDATE else 3)
    )
    return FinalizeRunResult(
        bundle_id=bundle_id,
        output_dir=root,
        status=status,
        exit_code=exit_code,
        outcome=context.selection.outcome.value,
        selected_profile_id=context.selection.selected_candidate_id if deployable else None,
        passport_id=passport.passport_id,
        synthetic=context.summary.synthetic_fixture,
    )
