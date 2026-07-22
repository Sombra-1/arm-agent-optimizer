from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aarchtune.cli import app
from aarchtune.evaluation.models import EvaluationConfig
from aarchtune.evaluation.runner import run_evaluation
from aarchtune.finalization.models import FinalizeConfig, OptimizationPassport, ReportData
from aarchtune.finalization.passport import _envelope_data, canonical_hash, verify_passport
from aarchtune.finalization.report import render_report
from aarchtune.finalization.runner import finalize_evaluation
from aarchtune.finalization.validation import validate_bundle
from aarchtune.optimization.models import OptimizationGoal
from aarchtune.optimization.planner import create_search_plan
from aarchtune.screening.capabilities import clear_bench_capability_cache
from aarchtune.screening.models import ScreeningConfig
from aarchtune.screening.runner import run_screening

cli = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[2]
FAKE_SERVER = REPOSITORY / "tests/fixtures/bin/fake-llama-server"
FAKE_BENCH = REPOSITORY / "tests/fixtures/bin/fake-llama-bench"
FAKE_MODEL = REPOSITORY / "tests/fixtures/models/fake-model.gguf"
WORKLOAD = REPOSITORY / "workloads/smoke-test.jsonl"


@pytest.fixture(scope="module")
def selected_evaluation(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("finalization-product")
    _, plan = create_search_plan(
        goal=OptimizationGoal.BALANCED,
        output_dir=root / "plan",
        binary=FAKE_SERVER,
        model=FAKE_MODEL,
        workload=WORKLOAD,
        maximum_profiles=4,
    )
    scenario = root / "scenario.yaml"
    scenario.write_text(
        "schema_version: '1.0'\nscenarios:\n"
        "  - {id: decode, prompt_tokens: 0, generation_tokens: 16}\n",
        encoding="utf-8",
    )
    previous_bench = os.environ.get("FAKE_LLAMA_BENCH_SCENARIO")
    previous_server = os.environ.get("FAKE_LLAMA_SCENARIO")
    os.environ["FAKE_LLAMA_BENCH_SCENARIO"] = "healthy-jsonl"
    os.environ["FAKE_LLAMA_SCENARIO"] = "profile-matrix"
    clear_bench_capability_cache()
    try:
        screening = run_screening(
            ScreeningConfig(
                plan_dir=plan,
                bench_binary=FAKE_BENCH,
                output_dir=root / "screening",
                scenario_path=scenario,
                advance_count=3,
                repetitions=1,
                invocation_timeout_seconds=2.0,
                total_timeout_seconds=60.0,
                sample_interval_seconds=0.05,
                allow_synthetic=True,
            )
        )
        assert screening.exit_code == 0
        evaluation = run_evaluation(
            EvaluationConfig(
                screening_dir=screening.output_dir,
                output_dir=root / "evaluation",
                repetitions=2,
                warmup_requests=1,
                request_timeout_seconds=0.3,
                startup_timeout_seconds=2.0,
                shutdown_timeout_seconds=0.5,
                sample_interval_seconds=0.05,
                maximum_total_duration_seconds=120.0,
                allow_synthetic=True,
            )
        )
        assert evaluation.exit_code == 0
        yield evaluation.output_dir
    finally:
        if previous_bench is None:
            os.environ.pop("FAKE_LLAMA_BENCH_SCENARIO", None)
        else:
            os.environ["FAKE_LLAMA_BENCH_SCENARIO"] = previous_bench
        if previous_server is None:
            os.environ.pop("FAKE_LLAMA_SCENARIO", None)
        else:
            os.environ["FAKE_LLAMA_SCENARIO"] = previous_server


@pytest.fixture(scope="module")
def final_bundle(tmp_path_factory: pytest.TempPathFactory, selected_evaluation: Path) -> Path:
    output = tmp_path_factory.mktemp("final-bundle") / "bundle"
    result = finalize_evaluation(
        FinalizeConfig(
            evaluation_dir=selected_evaluation,
            output_dir=output,
            allow_synthetic=True,
        )
    )
    assert result.exit_code == 0
    return output


def test_final_bundle_contains_deployment_evidence_without_raw_responses(
    final_bundle: Path,
) -> None:
    expected = {
        "bundle-manifest.json",
        "optimization-passport.json",
        "selected-profile.yaml",
        "selected-command.json",
        "run-optimized.sh",
        "reproduce-evaluation.sh",
        "pareto-frontier.json",
        "report-data.json",
        "report.html",
        "docker-compose-status.json",
        "checksums.sha256",
        "README.txt",
    }
    assert expected <= {item.name for item in final_bundle.iterdir()}
    assert not {"raw-attempts.jsonl", "server.log", "request-metrics.jsonl"}.intersection(
        item.name for item in final_bundle.iterdir()
    )


def test_bundle_passport_and_cli_validation(final_bundle: Path) -> None:
    assert validate_bundle(final_bundle).valid
    assert verify_passport(final_bundle / "optimization-passport.json").valid
    finalized = cli.invoke(app, ["finalize", "validate", str(final_bundle), "--json"])
    passport = cli.invoke(
        app,
        ["passport", "verify", str(final_bundle / "optimization-passport.json"), "--json"],
    )
    assert finalized.exit_code == 0
    assert passport.exit_code == 0
    assert json.loads(finalized.output)["valid"] is True
    assert json.loads(passport.output)["content_hash_valid"] is True


def test_finalize_cli_human_json_and_input_errors(
    tmp_path: Path, selected_evaluation: Path
) -> None:
    human_output = tmp_path / "human"
    human = cli.invoke(
        app,
        [
            "finalize",
            "--evaluation",
            str(selected_evaluation),
            "--output-dir",
            str(human_output),
            "--allow-synthetic",
        ],
    )
    assert human.exit_code == 0, human.output
    assert "Final Bundle Created" in human.output
    assert "Synthetic test evidence" in human.output
    assert "run-optimized.sh" in human.output.replace("\n", "")

    json_output = tmp_path / "json"
    machine = cli.invoke(
        app,
        [
            "finalize",
            "--evaluation",
            str(selected_evaluation),
            "--output-dir",
            str(json_output),
            "--allow-synthetic",
            "--json",
        ],
    )
    assert machine.exit_code == 0
    assert json.loads(machine.output)["outcome"] in {
        "candidate_selected",
        "baseline_retained",
    }
    missing = cli.invoke(app, ["finalize", "--evaluation", str(selected_evaluation)])
    assert missing.exit_code == 1
    rejected = cli.invoke(
        app,
        [
            "finalize",
            "--evaluation",
            str(selected_evaluation),
            "--output-dir",
            str(tmp_path / "rejected"),
        ],
    )
    assert rejected.exit_code == 1
    assert "allow-synthetic" in rejected.output


def test_validation_and_passport_cli_fail_cleanly(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    bundle = cli.invoke(app, ["finalize", "validate", str(invalid)])
    passport = cli.invoke(app, ["passport", "verify", str(invalid / "missing.json")])
    assert bundle.exit_code == 1
    assert "Final bundle invalid" in bundle.output
    assert passport.exit_code == 1
    assert "Optimization Passport invalid" in passport.output


def test_passport_has_full_provenance_and_no_response_bodies(final_bundle: Path) -> None:
    passport = OptimizationPassport.model_validate_json(
        (final_bundle / "optimization-passport.json").read_text()
    )
    assert passport.hardware["operating_system"]
    assert passport.hardware["kernel"]
    assert "kleidiai_evidence" in passport.runtime
    assert {item.stage for item in passport.stage_artifact_hashes} == {
        "baseline",
        "planning",
        "screening",
        "evaluation",
    }
    payload = (final_bundle / "optimization-passport.json").read_text()
    assert "raw_response" not in payload
    assert "time_to_first_token" in passport.unavailable_metrics
    assert passport.synthetic is True


def test_report_is_self_contained_accessible_and_synthetic(final_bundle: Path) -> None:
    report = (final_bundle / "report.html").read_text()
    for section in (
        "Selection outcome",
        "Candidate funnel",
        "Quality gate",
        "Pareto frontier",
        "Baseline drift sentinel",
        "Hardware and Arm capabilities",
        "Runtime and KleidiAI evidence",
        "Reproduction command",
        "Limitations",
        "Artifact integrity hashes",
    ):
        assert section in report
    assert "SYNTHETIC TEST EVIDENCE" in report
    assert "<svg" in report and "<table" in report
    assert "https://" not in report and "http://" not in report
    assert "raw-attempts" not in report


def test_report_rendering_is_deterministic(final_bundle: Path) -> None:
    data = ReportData.model_validate_json((final_bundle / "report-data.json").read_text())
    assert render_report(data) == render_report(data)
    assert "Selected profile quality by category" in render_report(data)
    assert "Selected profile quality by validator" in render_report(data)


def test_run_and_reproduction_scripts_use_safe_arrays(final_bundle: Path) -> None:
    run_script = final_bundle / "run-optimized.sh"
    reproduction = final_bundle / "reproduce-evaluation.sh"
    for path in (run_script, reproduction):
        text = path.read_text()
        assert text.startswith("#!/usr/bin/env bash\nset -euo pipefail")
        assert "args=(" in text
        assert '"${args[@]}"' in text
        assert "eval " not in text
        assert path.stat().st_mode & 0o100
    assert "127.0.0.1" in run_script.read_text()
    assert "sha256sum" in run_script.read_text()
    assert "Refusing to overwrite" in reproduction.read_text()


def test_no_container_image_produces_explicit_status(final_bundle: Path) -> None:
    status = json.loads((final_bundle / "docker-compose-status.json").read_text())
    assert status == {
        "available": False,
        "reason": "No container image was provided",
        "schema_version": "1.0",
    }
    assert not (final_bundle / "docker-compose.optimized.yaml").exists()


def test_explicit_container_image_produces_restricted_compose(
    tmp_path: Path, selected_evaluation: Path
) -> None:
    output = tmp_path / "compose-bundle"
    finalize_evaluation(
        FinalizeConfig(
            evaluation_dir=selected_evaluation,
            output_dir=output,
            allow_synthetic=True,
            container_image="ghcr.io/example/llama-cpp:test",
        )
    )
    compose = yaml.safe_load((output / "docker-compose.optimized.yaml").read_text())
    service = compose["services"]["llama-server"]
    assert service["image"] == "ghcr.io/example/llama-cpp:test"
    assert service["ports"] == ["127.0.0.1:8080:8080"]
    assert service["volumes"][0].endswith(":/models/model.gguf:ro")
    assert "privileged" not in service and "network_mode" not in service
    assert "healthcheck" in service
    assert validate_bundle(output).valid


def test_pareto_references_are_complete_and_deterministic(final_bundle: Path) -> None:
    pareto = json.loads((final_bundle / "pareto-frontier.json").read_text())
    ids = [item["candidate_id"] for item in pareto["records"]]
    assert ids == sorted(ids)
    assert any(item["baseline"] for item in pareto["records"])
    assert any(item["selected"] for item in pareto["records"])
    for item in pareto["records"]:
        assert set(item["dominating_candidate_ids"]) <= set(ids)


def test_checksum_and_passport_tampering_are_detected(tmp_path: Path, final_bundle: Path) -> None:
    checksum_copy = tmp_path / "checksum-tamper"
    shutil.copytree(final_bundle, checksum_copy)
    (checksum_copy / "README.txt").write_text("tampered", encoding="utf-8")
    assert not validate_bundle(checksum_copy).valid

    passport_copy = tmp_path / "passport-tamper"
    shutil.copytree(final_bundle, passport_copy)
    payload = json.loads((passport_copy / "optimization-passport.json").read_text())
    payload["synthetic"] = False
    (passport_copy / "optimization-passport.json").write_text(json.dumps(payload))
    verification = verify_passport(passport_copy / "optimization-passport.json")
    assert not verification.valid
    assert not verification.content_hash_valid


def test_missing_and_forbidden_bundle_files_are_detected(
    tmp_path: Path, final_bundle: Path
) -> None:
    copied = tmp_path / "missing"
    shutil.copytree(final_bundle, copied)
    (copied / "report.html").unlink()
    assert any("report.html" in error for error in validate_bundle(copied).errors)

    copied = tmp_path / "forbidden"
    shutil.copytree(final_bundle, copied)
    (copied / "raw-attempts.jsonl").write_text('{"text":"private"}\n')
    errors = validate_bundle(copied).errors
    assert any("Raw or forbidden" in error for error in errors)


@pytest.mark.parametrize(
    "mutation",
    [
        "external_url",
        "missing_disclaimer",
        "unsafe_script",
        "compose_conflict",
        "profile_mismatch",
        "command_mismatch",
        "temporary_file",
    ],
)
def test_bundle_validation_detects_cross_artifact_tampering(
    final_bundle: Path, mutation: str
) -> None:
    copied = final_bundle.parent / f"validation-{mutation}"
    if copied.exists():
        shutil.rmtree(copied)
    shutil.copytree(final_bundle, copied)
    if mutation == "external_url":
        with (copied / "report.html").open("a", encoding="utf-8") as target:
            target.write("https://unexpected.example")
    elif mutation == "missing_disclaimer":
        readme = copied / "README.txt"
        readme.write_text(
            readme.read_text().replace("specific to the recorded hardware", "portable"),
            encoding="utf-8",
        )
    elif mutation == "unsafe_script":
        script = copied / "run-optimized.sh"
        script.write_text("#!/usr/bin/env bash\neval unsafe\n", encoding="utf-8")
        script.chmod(0o644)
    elif mutation == "compose_conflict":
        (copied / "docker-compose.optimized.yaml").write_text("services: {}\n")
    elif mutation == "profile_mismatch":
        profile = yaml.safe_load((copied / "selected-profile.yaml").read_text())
        profile["profile_id"] = "different"
        (copied / "selected-profile.yaml").write_text(yaml.safe_dump(profile))
    elif mutation == "command_mismatch":
        command = json.loads((copied / "selected-command.json").read_text())
        command["arguments"].append("--unexpected")
        (copied / "selected-command.json").write_text(json.dumps(command))
    else:
        (copied / ".incomplete.tmp").write_text("partial")
    assert not validate_bundle(copied).valid


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("outcome", "outcome differs"),
        ("selection", "selected profile differs"),
        ("synthetic", "synthetic status differs"),
        ("runtime_hash", "runtime binary hash is missing"),
        ("disclaimer", "disclaimer is missing"),
        ("references", "selection reference is missing"),
    ],
)
def test_passport_cross_evidence_guards_survive_rehashed_tampering(
    final_bundle: Path, mutation: str, expected: str
) -> None:
    copied = final_bundle.parent / f"passport-{mutation}"
    if copied.exists():
        shutil.rmtree(copied)
    shutil.copytree(final_bundle, copied)
    passport_path = copied / "optimization-passport.json"
    payload = json.loads(passport_path.read_text())
    if mutation == "outcome":
        payload["outcome"] = (
            "candidate_selected"
            if payload["outcome"] == "baseline_retained"
            else "baseline_retained"
        )
    elif mutation == "selection":
        payload["selected_profile"]["candidate_id"] = "different"
    elif mutation == "synthetic":
        payload["synthetic"] = False
    elif mutation == "runtime_hash":
        payload["runtime"]["fingerprint"]["binary_sha256"] = "missing"
    elif mutation == "disclaimer":
        payload["hardware_specific_disclaimer"] = "portable"
    else:
        payload["stage_artifact_hashes"] = []
    unsigned = dict(payload)
    unsigned.pop("passport_content_hash")
    payload["passport_content_hash"] = canonical_hash(unsigned)
    passport_path.write_text(json.dumps(payload), encoding="utf-8")
    verification = verify_passport(passport_path)
    assert not verification.valid
    assert verification.content_hash_valid
    assert any(expected in error for error in verification.errors)


def test_report_omits_unavailable_metrics_instead_of_plotting_zero(final_bundle: Path) -> None:
    data = ReportData.model_validate_json((final_bundle / "report-data.json").read_text())
    unavailable = data.model_copy(
        update={
            "candidates": [
                {
                    **candidate,
                    "requests_per_minute": None,
                    "p95_latency_seconds": None,
                    "peak_rss_bytes": None,
                }
                for candidate in data.candidates
            ],
            "fastest_rejected": None,
            "pareto": data.pareto.model_copy(update={"records": []}),
        }
    )
    report = render_report(unavailable)
    assert report.count("Metric unavailable for all candidates") == 3
    assert "Pareto metrics unavailable" in report


def test_envelope_reader_handles_missing_and_non_object_data(tmp_path: Path) -> None:
    assert _envelope_data(tmp_path / "missing.json") == {}
    array = tmp_path / "array.json"
    array.write_text("[]")
    assert _envelope_data(array) == {}
    nested = tmp_path / "nested.json"
    nested.write_text('{"data": []}')
    assert _envelope_data(nested) == {}


@pytest.mark.parametrize("mutation", ["schema", "checksums", "manifest_state"])
def test_bundle_validation_reports_malformed_core_artifacts(
    final_bundle: Path, mutation: str
) -> None:
    copied = final_bundle.parent / f"malformed-{mutation}"
    if copied.exists():
        shutil.rmtree(copied)
    shutil.copytree(final_bundle, copied)
    if mutation == "schema":
        (copied / "bundle-manifest.json").write_text("{}")
    elif mutation == "checksums":
        (copied / "checksums.sha256").write_text("not a checksum\n")
    else:
        path = copied / "bundle-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["status"] = "initializing"
        manifest["validation_status"] = "pending"
        path.write_text(json.dumps(manifest))
    assert not validate_bundle(copied).valid
