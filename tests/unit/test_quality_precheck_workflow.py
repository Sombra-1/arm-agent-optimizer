from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from aarchtune.baseline.models import BaselineRunConfig
from aarchtune.baseline.runner import run_baseline
from aarchtune.runtime.config import ResponseFormatMode

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-arm64-quality-precheck.yml"
POLICY = ROOT / "configs/default-quality-policy.yaml"


def _helper() -> ModuleType:
    path = ROOT / "scripts/quality_precheck.py"
    spec = importlib.util.spec_from_file_location("quality_precheck", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict[str, object]:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _workflow_step(name: str) -> dict[str, str]:
    steps = _workflow()["jobs"]["quality-precheck"]["steps"]
    return next(item for item in steps if item.get("name") == name)


def _baseline(
    tmp_path: Path,
    fake_binary: Path,
    fake_model: Path,
    *,
    scenario: str = "healthy-with-timings",
    response_format_mode: ResponseFormatMode = ResponseFormatMode.NONE,
) -> Path:
    output = tmp_path / scenario
    result = run_baseline(
        BaselineRunConfig(
            binary_path=fake_binary,
            model_path=fake_model,
            workload_path=ROOT / "workloads/smoke-test.jsonl",
            output_dir=output,
            repetitions=2,
            warmup_requests=1,
            response_format_mode=response_format_mode,
            request_timeout_seconds=0.5,
            startup_timeout_seconds=2.0,
            shutdown_timeout_seconds=0.5,
            sample_interval_seconds=0.05,
            extra_environment={"FAKE_LLAMA_SCENARIO": scenario},
        )
    )
    assert result.exit_code == 0
    return output


def test_model_profiles_are_allowlisted_immutable_and_exact() -> None:
    helper = _helper()
    workflow = _workflow()
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]["model_profile"]
    assert dispatch["default"] == "qwen2.5-14b-q3-k-m"
    assert dispatch["options"] == [
        "mistral-nemo-12b-q4-k-m",
        "qwen2.5-14b-q3-k-m",
        "qwen2.5-7b-q3-k-m",
    ]

    seven = helper.get_model_profile("qwen2.5-7b-q3-k-m")
    assert seven.repository == "Qwen/Qwen2.5-7B-Instruct-GGUF"
    assert seven.revision == "293ca9a10157b0e5fc5cb32af8b636a88bede891"
    assert seven.filename == "qwen2.5-7b-instruct-q3_k_m.gguf"
    assert seven.size_bytes == 3_808_391_072
    assert seven.sha256 == ("a96b16179dc6cc9afdf0cf7a96a80c199cbd00b9be207c3465be21cb721cca5e")
    assert seven.license_evidence.repository == "Qwen/Qwen2.5-7B-Instruct-GGUF"
    assert seven.license_evidence.revision == "74ef91efd0899612867d6bb080ce5a2788ef6aa1"
    assert seven.license_evidence.path == "README.md"
    assert seven.license_evidence.sha256 == (
        "b813de546b5f0a1d8c49307d428582fd649be627d9b33a60ecf807a3381af05e"
    )
    assert seven.license_evidence.license_id == "apache-2.0"
    fourteen = helper.get_model_profile("qwen2.5-14b-q3-k-m")
    assert fourteen.repository == "bartowski/Qwen2.5-14B-Instruct-GGUF"
    assert fourteen.revision == "05244aa5d871c661c80082a15d3bce44714d068d"
    assert fourteen.filename == "Qwen2.5-14B-Instruct-Q3_K_M.gguf"
    assert fourteen.quantization == "Q3_K_M"
    assert fourteen.size_bytes == 7_339_204_736
    assert fourteen.sha256 == ("2f68ac3ba018f7de7641229f19adafde5e59d02bbf5651fdbcc510bb9f3facca")
    assert fourteen.license == "Apache-2.0"
    assert fourteen.license_evidence.revision == "05244aa5d871c661c80082a15d3bce44714d068d"
    assert fourteen.parameter_class == "14B"
    mistral = helper.get_model_profile("mistral-nemo-12b-q4-k-m")
    assert mistral.repository == "bartowski/Mistral-Nemo-Instruct-2407-GGUF"
    assert mistral.revision == "a2dd64a0a76ea1bdb2bb6ab6fa5496b003c7c908"
    assert mistral.filename == "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf"
    assert mistral.quantization == "Q4_K_M"
    assert mistral.size_bytes == 7_477_208_192
    assert mistral.sha256 == ("7c1a10d202d8788dbe5628dc962254d10654c853cae6aaeca0618f05490d4a46")
    assert mistral.source_model.repository == "mistralai/Mistral-Nemo-Instruct-2407"
    assert mistral.source_model.revision == "04d8a90549d23fc6bd7f642064003592df51e9b3"
    assert mistral.license_evidence.sha256 == (
        "987b63374b1441d14b35efa83705bd6732768f53f8e5b731f818d7181e1f5b2e"
    )
    assert mistral.architecture == "llama"
    assert mistral.tokenizer_model == "gpt2"
    assert mistral.requires_initial_system_support is True
    for profile in (seven, fourteen, mistral):
        assert len(profile.revision) == 40
        assert profile.revision not in {"main", "latest"}
        assert f"/resolve/{profile.revision}/" in profile.url
        assert "/main/" not in profile.url
        assert "latest" not in profile.url
        assert len(profile.license_evidence.revision) == 40
        assert f"/resolve/{profile.license_evidence.revision}/" in profile.license_evidence.url
        assert len(profile.source_model.revision) == 40
    with pytest.raises(ValueError, match="not allowlisted"):
        helper.get_model_profile("arbitrary/repository")


def _license_evidence(
    helper: ModuleType,
    payload: bytes,
    *,
    revision: str = "a" * 40,
    license_id: str = "apache-2.0",
) -> object:
    return helper.LicenseEvidence(
        repository="Qwen/Qwen2.5-7B-Instruct-GGUF",
        revision=revision,
        path="README.md",
        sha256=hashlib.sha256(payload).hexdigest(),
        license_id=license_id,
        license_name="Apache-2.0",
    )


def test_null_card_data_does_not_override_valid_pinned_license_evidence(
    tmp_path: Path,
) -> None:
    helper = _helper()
    model_metadata = {
        "sha": "293ca9a10157b0e5fc5cb32af8b636a88bede891",
        "cardData": {"license": None},
    }
    assert model_metadata["cardData"]["license"] is None
    payload = b"---\nlicense: apache-2.0\n---\nPrivate model card details.\n"
    source = tmp_path / "README.md"
    source.write_bytes(payload)
    result = helper.verify_license(source, _license_evidence(helper, payload))
    assert result["license_id"] == "apache-2.0"
    assert result["license_verified"] is True
    assert "contents" not in result


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"---\nname: test\n---\n", "license field is missing"),
        (b"---\nlicense: null\n---\n", "license field is null or empty"),
        (b"---\nlicense: mit\n---\n", "license ID mismatch"),
        (b"license: apache-2.0\n", "front matter is missing"),
        (b"---\nlicense: [\n---\n", "front matter is malformed"),
    ],
)
def test_license_evidence_requires_valid_matching_yaml_front_matter(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    helper = _helper()
    source = tmp_path / "README.md"
    source.write_bytes(payload)
    with pytest.raises(ValueError, match=message):
        helper.verify_license(source, _license_evidence(helper, payload))


def test_license_evidence_rejects_missing_file_hash_mismatch_and_mutable_revision(
    tmp_path: Path,
) -> None:
    helper = _helper()
    payload = b"---\nlicense: apache-2.0\n---\n"
    evidence = _license_evidence(helper, payload)
    missing = tmp_path / "missing.md"
    with pytest.raises(ValueError, match="does not exist"):
        helper.verify_license(missing, evidence)

    source = tmp_path / "README.md"
    source.write_bytes(payload)
    wrong_hash = replace(evidence, sha256="0" * 64)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        helper.verify_license(source, wrong_hash)
    for revision in ("main", "latest"):
        mutable = replace(evidence, revision=revision)
        with pytest.raises(ValueError, match="not immutable"):
            helper.verify_license(source, mutable)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ({"revision": "main"}, "revision is not immutable"),
        ({"revision": "latest"}, "revision is not immutable"),
        ({"path": ""}, "path is empty"),
        ({"sha256": "invalid"}, "SHA-256 is malformed"),
        ({"license_id": ""}, "ID is empty"),
    ],
)
def test_model_profile_rejects_invalid_license_provenance(
    replacement: dict[str, str],
    message: str,
) -> None:
    helper = _helper()
    profile = helper.MODEL_PROFILES["qwen2.5-7b-q3-k-m"]
    helper.MODEL_PROFILES["invalid-license"] = replace(
        profile,
        license_evidence=replace(profile.license_evidence, **replacement),
    )
    with pytest.raises(ValueError, match=message):
        helper.get_model_profile("invalid-license")


@pytest.mark.parametrize(
    ("profile_update", "source_update", "message"),
    [
        ({"size_bytes": 0}, {}, "artifact identity is invalid"),
        ({"sha256": ""}, {}, "artifact identity is invalid"),
        ({}, {"revision": "main"}, "source model revision is not immutable"),
        ({}, {"repository": ""}, "source model provenance is incomplete"),
        ({}, {"model_name": ""}, "source model provenance is incomplete"),
    ],
)
def test_model_profile_rejects_invalid_artifact_or_source_provenance(
    profile_update: dict[str, object],
    source_update: dict[str, str],
    message: str,
) -> None:
    helper = _helper()
    profile = helper.MODEL_PROFILES["mistral-nemo-12b-q4-k-m"]
    helper.MODEL_PROFILES["invalid-provenance"] = replace(
        profile,
        source_model=replace(profile.source_model, **source_update),
        **profile_update,
    )
    with pytest.raises(ValueError, match=message):
        helper.get_model_profile("invalid-provenance")


def _write_synthetic_gguf(path: Path, metadata: dict[str, str]) -> None:
    with path.open("wb") as output:
        output.write(b"GGUF")
        output.write(struct.pack("<IQQ", 3, 0, len(metadata)))
        for key, value in metadata.items():
            key_bytes = key.encode()
            value_bytes = value.encode()
            output.write(struct.pack("<Q", len(key_bytes)))
            output.write(key_bytes)
            output.write(struct.pack("<IQ", 8, len(value_bytes)))
            output.write(value_bytes)


def test_mistral_gguf_metadata_and_system_template_are_verified(tmp_path: Path) -> None:
    helper = _helper()
    profile = helper.get_model_profile("mistral-nemo-12b-q4-k-m")
    model = tmp_path / profile.filename
    _write_synthetic_gguf(
        model,
        {
            "general.architecture": "llama",
            "general.name": "Mistral Nemo Instruct 2407",
            "tokenizer.ggml.model": "gpt2",
            "tokenizer.chat_template": (
                '{% if messages[0]["role"] == "system" %}'
                '{{ "[INST]" + messages[0]["content"] }}{% endif %}'
            ),
        },
    )
    result = helper.verify_gguf_metadata(model, profile)
    assert result["architecture"] == "llama"
    assert result["tokenizer_model"] == "gpt2"
    assert result["native_chat_template_present"] is True
    assert result["initial_system_message_supported"] is True
    assert "chat_template" not in result

    wrong = replace(profile, architecture="qwen2")
    with pytest.raises(ValueError, match="architecture mismatch"):
        helper.verify_gguf_metadata(model, wrong)


def test_output_contracts_are_allowlisted_and_dispatch_is_typed() -> None:
    helper = _helper()
    workflow = _workflow()
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]["output_contract"]
    assert dispatch == {
        "description": "Output contract",
        "required": "true",
        "type": "choice",
        "default": "prompt_only",
        "options": ["prompt_only", "json_object"],
    }
    assert helper.output_contract_environment("prompt_only") == {
        "OUTPUT_CONTRACT": "prompt_only",
        "RESPONSE_FORMAT_MODE": "none",
    }
    assert helper.output_contract_environment("json_object") == {
        "OUTPUT_CONTRACT": "json_object",
        "RESPONSE_FORMAT_MODE": "json_object",
    }
    with pytest.raises(ValueError, match="not allowlisted"):
        helper.output_contract_environment('{"type":"json_schema"}')


def test_profile_environment_preserves_spaces_without_shell_activation(tmp_path: Path) -> None:
    step = _workflow_step("Resolve allowlisted immutable model profile")["run"]
    github_environment = tmp_path / "github.env"
    runner_temp = tmp_path / "runner"
    model_dir = tmp_path / "models"
    runner_temp.mkdir()
    subprocess.run(
        ["bash", "-c", step],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
            "GITHUB_ENV": str(github_environment),
            "RUNNER_TEMP": str(runner_temp),
            "MODEL_PROFILE": "mistral-nemo-12b-q4-k-m",
            "OUTPUT_CONTRACT": "prompt_only",
            "MODEL_DIR": str(model_dir),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    records = github_environment.read_text(encoding="utf-8").splitlines()
    assert records.count("MODEL_LABEL=Mistral-Nemo-Instruct-2407 12B Q4_K_M") == 1
    assert 'source "$profile_environment"' not in step
    assert "eval " not in step
    assert "model_filename=$(" in step
    assert "sed -n 's/^MODEL_FILENAME=//p'" in step
    filename = next(
        record.removeprefix("MODEL_FILENAME=")
        for record in records
        if record.startswith("MODEL_FILENAME=")
    )
    assert filename == "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf"
    assert records.count(f"MODEL_PATH={model_dir}/{filename}") == 1
    assert not (runner_temp / "model-profile.env").exists()


@pytest.mark.parametrize("separator", ["\n", "\r"])
def test_workflow_environment_rejects_multiline_injection(separator: str) -> None:
    helper = _helper()
    profile = helper.MODEL_PROFILES["mistral-nemo-12b-q4-k-m"]
    helper.MODEL_PROFILES["injected"] = replace(
        profile,
        model_label=f"safe{separator}INJECTED=value",
    )
    with pytest.raises(ValueError, match="workflow environment value is multiline"):
        helper.profile_environment("injected")
    with pytest.raises(ValueError, match="workflow environment key is invalid"):
        helper.validate_environment({"unsafe": "value"})


def _run_cleanup(tmp_path: Path, environment: dict[str, str]) -> dict[str, str]:
    runner_temp = tmp_path / "runner"
    evidence_dir = tmp_path / "evidence"
    runner_temp.mkdir(exist_ok=True)
    completed = subprocess.run(
        [
            "bash",
            "-c",
            _workflow_step("Clean up workflow-owned processes and sensitive evidence")["run"],
        ],
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "RUNNER_TEMP": str(runner_temp),
            "EVIDENCE_DIR": str(evidence_dir),
            **environment,
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return dict(
        line.split("=", 1) for line in (evidence_dir / "cleanup-proof.txt").read_text().splitlines()
    )


def test_cleanup_succeeds_before_profile_completion(tmp_path: Path) -> None:
    proof = _run_cleanup(tmp_path, {})
    assert proof["model_path_initialized"] == "false"
    assert proof["model_download_started"] == "false"
    assert proof["model_deleted"] == "not_created"
    assert proof["cleanup_complete"] == "true"


@pytest.mark.parametrize("model_exists", [False, True])
def test_cleanup_handles_partially_initialized_model_path(
    tmp_path: Path, model_exists: bool
) -> None:
    model_path = tmp_path / "models" / "model.gguf"
    if model_exists:
        model_path.parent.mkdir()
        model_path.write_bytes(b"partial")
    proof = _run_cleanup(tmp_path, {"MODEL_PATH": str(model_path)})
    assert proof["model_path_initialized"] == "true"
    assert proof["model_deleted"] == ("true" if model_exists else "not_created")
    assert proof["model_cache_deleted"] == "not_created"
    assert proof["response_evidence_deleted"] == "not_created"
    assert proof["cleanup_complete"] == "true"
    assert not model_path.exists()


def test_model_size_and_sha_mismatches_fail(tmp_path: Path) -> None:
    helper = _helper()
    model = tmp_path / "model.gguf"
    with pytest.raises(ValueError, match="does not exist"):
        helper.verify_model(model, 0, "0" * 64)
    model.write_bytes(b"not a model")
    with pytest.raises(ValueError, match="size mismatch"):
        helper.verify_model(model, 1, "0" * 64)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        helper.verify_model(model, model.stat().st_size, "0" * 64)


def test_existing_policy_passes_complete_synthetic_baseline(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    helper = _helper()
    baseline = _baseline(tmp_path, fake_binary, fake_model)
    public = tmp_path / "public"
    outcome = helper.sanitize_baseline(baseline, POLICY, public)
    assert outcome == "quality_policy_passed"
    policy = json.loads((public / "quality-policy-outcome.json").read_text())
    assert policy["outcome"] == "quality_policy_passed"
    assert policy["aggregate"]["completed_attempts"] == 10
    assert policy["thresholds"]["absolute_minimums"]["task_success_rate"] == 0.95
    assert not any("response" in path.name for path in public.iterdir())
    serialized = " ".join(path.read_text() for path in public.iterdir())
    assert '"response_text"' not in serialized
    assert '"server_fields"' not in serialized
    assert '"observed"' not in serialized
    assert '"response_format_mode": "none"' in serialized
    assert '"response_format_applied": false' in serialized
    assert '"messages"' not in serialized


def test_json_object_provenance_is_sanitized_without_request_bodies(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    helper = _helper()
    baseline = _baseline(
        tmp_path,
        fake_binary,
        fake_model,
        response_format_mode=ResponseFormatMode.JSON_OBJECT,
    )
    public = tmp_path / "public-json"
    outcome = helper.sanitize_baseline(baseline, POLICY, public, "json_object")
    assert outcome == "quality_policy_passed"
    quality = json.loads((public / "quality-policy-outcome.json").read_text())
    assert quality["aggregate"]["output_contract"] == "json_object"
    assert quality["aggregate"]["response_format_mode"] == "json_object"
    assert quality["aggregate"]["response_format_applied"] is True
    assert quality["aggregate"]["response_format_type"] == "json_object"
    assert quality["interpretation"]["response_format_accepted"] is True
    provenance = json.loads((public / "request-provenance.json").read_text())
    assert provenance["measured_request_count"] == 10
    assert provenance["temperature_values"] == [0.0]
    assert provenance["seed_values"] == [42]
    assert provenance["stream_values"] == [False]
    assert provenance["response_format_applied_count"] == 10
    assert provenance["response_format_type_values"] == ["json_object"]
    assert provenance["max_tokens_by_task"] == {
        "smoke-contradiction-001": [100],
        "smoke-incident-001": [120],
        "smoke-planning-001": [140],
        "smoke-recovery-001": [100],
        "smoke-summary-001": [160],
    }
    serialized = " ".join(path.read_text() for path in public.iterdir())
    assert '"messages"' not in serialized
    assert '"response_text"' not in serialized
    assert '"server_fields"' not in serialized


def test_missing_attempt_is_incomplete_not_pass(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    helper = _helper()
    baseline = _baseline(tmp_path, fake_binary, fake_model)
    raw = baseline / "raw-attempts.jsonl"
    lines = raw.read_text(encoding="utf-8").splitlines()
    raw.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    public = tmp_path / "public"
    outcome = helper.sanitize_baseline(baseline, POLICY, public)
    report = json.loads((public / "quality-policy-outcome.json").read_text())
    assert outcome == "quality_evidence_incomplete"
    assert report["evidence_errors"]


def test_mismatched_measured_contract_is_incomplete_not_pass(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    helper = _helper()
    baseline = _baseline(
        tmp_path,
        fake_binary,
        fake_model,
        response_format_mode=ResponseFormatMode.JSON_OBJECT,
    )
    metrics = baseline / "request-metrics.jsonl"
    records = metrics.read_text(encoding="utf-8").splitlines()
    first = json.loads(records[0])
    first["request"]["response_format_mode"] = "none"
    first["request"]["response_format_applied"] = False
    first["request"]["response_format_type"] = None
    records[0] = json.dumps(first)
    metrics.write_text("\n".join(records) + "\n", encoding="utf-8")
    public = tmp_path / "mismatched-public"
    outcome = helper.sanitize_baseline(baseline, POLICY, public, "json_object")
    report = json.loads((public / "quality-policy-outcome.json").read_text())
    assert outcome == "quality_evidence_incomplete"
    assert "a measured request used the wrong response-format contract" in report["evidence_errors"]


def test_failed_validators_are_preserved_without_response_content(
    tmp_path: Path, fake_binary: Path, fake_model: Path
) -> None:
    helper = _helper()
    baseline = _baseline(
        tmp_path,
        fake_binary,
        fake_model,
        scenario="mixed-task-quality",
    )
    public = tmp_path / "public"
    outcome = helper.sanitize_baseline(baseline, POLICY, public)
    classifications = json.loads(
        (public / "per-task-classifications.json").read_text(encoding="utf-8")
    )
    incident = next(item for item in classifications if item["task_id"] == "smoke-incident-001")
    assert outcome == "quality_policy_failed"
    assert all("exact_value" in item["failed_validators"] for item in incident["attempts"])
    assert all("wrong_allowed_value" in item["classifications"] for item in incident["attempts"])
    assert incident["attempts_completed"] == 2
    assert incident["attempts_passed"] == 0
    assert incident["classifications"]["wrong_allowed_value"] == 2
    assert "unknown" not in json.dumps(classifications)


def test_probe_resource_incompatibility_is_distinct_from_quality_failure() -> None:
    helper = _helper()
    assert helper.classify_probe_result(0, "") == (
        "model_loaded",
        "quality_evidence_incomplete",
    )
    assert helper.classify_probe_result(137, "Killed") == (
        "resource_incompatible",
        "resource_incompatible",
    )
    assert helper.classify_probe_result(1, "std::bad_alloc") == (
        "resource_incompatible",
        "resource_incompatible",
    )
    assert helper.classify_probe_result(124, "") == (
        "probe_timeout",
        "quality_evidence_incomplete",
    )
    assert helper.classify_probe_result(2, "unrelated failure") == (
        "model_probe_failed",
        "model_runtime_incompatible",
    )


def test_workflow_is_baseline_only_and_cleanup_always_runs() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = _workflow()
    steps = workflow["jobs"]["quality-precheck"]["steps"]
    cleanup = next(
        item
        for item in steps
        if item.get("name") == "Clean up workflow-owned processes and sensitive evidence"
    )
    privacy = next(
        item for item in steps if item.get("name") == "Write summary and privacy-scan artifact"
    )
    upload = next(
        item
        for item in steps
        if item.get("name") == "Upload sanitized native Arm64 quality evidence"
    )
    assert cleanup["if"] == "always()"
    assert privacy["if"] == "always()"
    assert upload["if"] == "always() && steps.privacy_scan.outcome == 'success'"
    assert "aarchtune baseline \\" in text
    assert "aarchtune optimize " not in text
    assert "aarchtune screen " not in text
    assert "aarchtune evaluate " not in text
    assert "aarchtune plan " not in text
    assert "llama-bench" not in text
    assert "pkill" not in text
    assert "killall" not in text
    assert "raw-attempts.jsonl" in text
    assert "retention-days: 14" in text
    assert "resource_incompatible" in text
    assert "response_format_unsupported" in text
    assert "model_runtime_incompatible" in text
    assert "Verify immutable source-model provenance" in text
    assert "source-model-provenance.json" in text
    assert "verify-gguf-metadata" in text
    assert "LLM_CHAT_TEMPLATE_MISTRAL_V3_TEKKEN" in text
    assert ".cardData.license" not in text
    assert "Verify immutable license evidence" in text
    assert ".size == $size and .lfs.size == $size and .lfs.sha256 == $sha256" in text
    assert '--profile "$MODEL_PROFILE"' in text
    assert '--path "$license_source"' in text
    assert '"$RUNNER_TEMP/model-license-evidence"' in text
    assert '> "$EVIDENCE_DIR/license-provenance.json"' in text
    assert "LICENSE_EVIDENCE_URL" in text
    assert '--response-format "$RESPONSE_FORMAT_MODE"' in text
    assert 'if (response_type == "json_object")' in text
    assert 'json_schema = json_value(response_format, "schema", json::object());' in text
    assert "params.grammar = json_schema_to_grammar" in text
    assert "--grammar" not in text
    assert "--json-schema" not in text
    assert "pkill" not in text
    assert "killall" not in text


def test_license_source_is_cleaned_and_only_sanitized_provenance_is_public() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'license_source="$RUNNER_TEMP/model-license-evidence"' in text
    assert '"$RUNNER_TEMP/model-license-evidence" \\' in text
    assert '[[ ! -e "$RUNNER_TEMP/model-license-evidence" ]]' in text
    assert 'echo "license_evidence_source_deleted=$license_evidence_source_deleted"' in text
    assert "$EVIDENCE_DIR/model-license-evidence" not in text
    assert "full README" not in text
    provenance_block = text[
        text.index("jq -n \\", text.index("Download and verify pinned model")) : text.index(
            "Probe model loading on CPU"
        )
    ]
    assert "LICENSE_EVIDENCE_URL" not in provenance_block


def test_public_binary_checksums_use_relative_names(tmp_path: Path) -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'cd "$LLAMA_BUILD_DIR/bin"' in text
    assert "sha256sum llama-server llama-cli" in text
    assert 'sha256sum "$path"' not in text

    binary_dir = tmp_path / "absolute" / "runner" / "build" / "bin"
    binary_dir.mkdir(parents=True)
    (binary_dir / "llama-server").write_bytes(b"server")
    (binary_dir / "llama-cli").write_bytes(b"cli")
    result = subprocess.run(
        [
            "bash",
            "-c",
            '(cd "$1" && sha256sum llama-server llama-cli)',
            "checksum-test",
            str(binary_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "/home/runner/" not in result.stdout
    assert "/opt/hostedtoolcache/" not in result.stdout
    assert str(tmp_path) not in result.stdout
    assert "llama-server" in result.stdout
    assert "llama-cli" in result.stdout
    assert len(result.stdout.splitlines()[0].split()[0]) == 64
    assert len(result.stdout.splitlines()[1].split()[0]) == 64


def _matches(pattern: str, text: str) -> bool:
    result = subprocess.run(
        ["grep", "-Eq", pattern],
        input=text,
        text=True,
        check=False,
    )
    return result.returncode == 0


def test_privacy_scan_allows_placeholders_and_rejects_values() -> None:
    environment = _workflow()["jobs"]["quality-precheck"]["env"]
    general = environment["PUBLIC_EVIDENCE_SCAN_PATTERN"]
    assignment = environment["HF_TOKEN_ASSIGNMENT_PATTERN"]
    placeholders = [
        "value from HF_TOKEN environment variable",
        "--hf-token <token>",
        "HF_TOKEN=<token>",
        '"HF_TOKEN": "<token>"',
    ]
    assert all(not _matches(general, value) for value in placeholders)
    assert all(not _matches(assignment, value) for value in placeholders)
    assert _matches(assignment, "HF_TOKEN=actual-value")
    assert _matches(assignment, '"HF_TOKEN": "actual-value"')
    assert _matches(general, "Authorization: Bearer actual-value")
    assert _matches(general, "Bearer actual-value")
    assert _matches(general, "/home/runner/work/_temp/private")
    assert _matches(general, "/opt/hostedtoolcache/Python")
    assert _matches(general, "/home/ayx/private")
    assert _matches(general, "/tmp/a0b1c2d3-1234-5678-9012-abcdefabcdef")


def test_historical_comparison_is_labelled_and_not_combined(tmp_path: Path) -> None:
    helper = _helper()
    quality = tmp_path / "quality-policy-outcome.json"
    quality.write_text(
        json.dumps(
            {
                "outcome": "quality_policy_failed",
                "aggregate": {
                    "request_success_rate": 1.0,
                    "task_success_rate": 0.6,
                    "json_validity_rate": 0.9,
                    "validator_pass_rate": 0.8,
                    "timeout_rate": 0.0,
                    "completed_attempt_fraction": 1.0,
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "comparison.json"
    helper.write_comparison_summary(
        "qwen2.5-14b-q3-k-m",
        "json_object",
        quality,
        output,
        123,
    )
    comparison = json.loads(output.read_text(encoding="utf-8"))
    assert comparison["benchmark"] is False
    assert comparison["statistical_combination"] is False
    assert comparison["current"]["task_success_rate"] == 0.6
    assert comparison["current"]["output_contract"] == "json_object"
    assert [item["task_success_rate"] for item in comparison["historical_references"]] == [
        0.0,
        0.4,
        0.4,
        0.4,
    ]
    assert [item["output_contract"] for item in comparison["historical_references"]] == [
        "prompt_only",
        "prompt_only",
        "prompt_only",
        "json_object",
    ]
    assert all(
        item["source"] == "separate earlier native run"
        for item in comparison["historical_references"]
    )
