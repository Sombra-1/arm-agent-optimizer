from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from aarchtune.baseline.errors import BaselineInputError
from aarchtune.baseline.models import BaselineRunConfig
from aarchtune.baseline.runner import run_baseline
from aarchtune.optimization.identity import profile_hash, stable_hash
from aarchtune.optimization.models import ProfileRuntime
from aarchtune.runtime.client import LlamaServerClient
from aarchtune.runtime.config import ResponseFormatMode
from aarchtune.runtime.errors import ResponseFormatUnsupportedError, RuntimeManagementError
from aarchtune.workload.evaluation import evaluate_workload
from aarchtune.workload.loader import load_workload
from aarchtune.workload.schema import LoadedWorkload, WorkloadTask

ROOT = Path(__file__).resolve().parents[2]


def _task() -> WorkloadTask:
    return WorkloadTask.model_validate_json(
        json.dumps(
            {
                "id": "classification",
                "category": "classification",
                "description": "Classify a ticket",
                "messages": [{"role": "user", "content": "Classify this ticket. Return JSON."}],
                "generation": {"temperature": 0.0, "max_tokens": 64, "seed": 42},
                "output_schema": {
                    "type": "object",
                    "properties": {"queue": {"type": "string"}},
                    "required": ["queue"],
                    "additionalProperties": False,
                },
                "validators": [
                    {"type": "valid_json"},
                    {
                        "type": "exact_value",
                        "path": "$.queue",
                        "expected": "private-expected-label",
                    },
                ],
            }
        )
    )


def test_schema_payload_uses_explicit_contract_and_preserves_semantic_validation() -> None:
    task = _task()
    captured: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"queue":"wrong"}'}}]}
        )

    with LlamaServerClient(
        "127.0.0.1", 1, response_format_mode=ResponseFormatMode.JSON_SCHEMA
    ) as client:
        client._client.close()
        client._client = httpx.Client(
            transport=httpx.MockTransport(handle), base_url=client.base_url
        )
        response = client.chat_completion(task)
    payload = captured[0]
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "workload_output", "schema": task.output_schema},
    }
    assert "private-expected-label" not in json.dumps(payload)
    assert payload["stream"] is False and payload["seed"] == 42 and payload["max_tokens"] == 64
    workload = LoadedWorkload(path=Path("unused"), sha256="test", byte_size=0, tasks=[task])
    evaluated = evaluate_workload(workload, [response])
    assert evaluated.json_validity_rate == 1.0 and evaluated.task_success_rate == 0.0


@pytest.mark.parametrize("status", [400, 422])
def test_schema_rejection_has_no_silent_fallback(status: int) -> None:
    calls: list[httpx.Request] = []

    def reject(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": "unsupported schema"})

    with LlamaServerClient(
        "127.0.0.1", 1, response_format_mode=ResponseFormatMode.JSON_SCHEMA
    ) as client:
        client._client.close()
        client._client = httpx.Client(
            transport=httpx.MockTransport(reject), base_url=client.base_url
        )
        with pytest.raises(ResponseFormatUnsupportedError, match="json_schema"):
            client.chat_completion(_task())
    assert len(calls) == 1


def test_missing_output_schema_is_rejected_before_http() -> None:
    task = _task().model_copy(update={"output_schema": None})
    with (
        LlamaServerClient(
            "127.0.0.1", 1, response_format_mode=ResponseFormatMode.JSON_SCHEMA
        ) as client,
        pytest.raises(RuntimeManagementError, match="explicit output_schema"),
    ):
        client.chat_completion(task)


@pytest.mark.parametrize("schema", [{"type": "invalid"}, {"$ref": "https://example.com/schema"}])
def test_output_schema_uses_existing_strict_validation(schema: dict[str, str]) -> None:
    data = _task().model_dump(mode="json")
    data["output_schema"] = schema
    with pytest.raises(ValidationError):
        WorkloadTask.model_validate_json(json.dumps(data))


def test_baseline_checks_all_task_schemas_before_inference(tmp_path: Path) -> None:
    output = tmp_path / "should-not-exist"
    with pytest.raises(BaselineInputError, match="every task"):
        run_baseline(
            BaselineRunConfig(
                binary_path=ROOT / "tests/fixtures/bin/fake-llama-server",
                model_path=ROOT / "tests/fixtures/models/fake-model.gguf",
                workload_path=ROOT / "workloads/smoke-test.jsonl",
                output_dir=output,
                response_format_mode=ResponseFormatMode.JSON_SCHEMA,
            )
        )
    assert not output.exists()


def test_schema_mode_records_exact_request_contract(tmp_path: Path) -> None:
    task = load_workload(ROOT / "workloads/smoke-test.jsonl").tasks[0]
    data = task.model_dump(mode="json", by_alias=True)
    data["output_schema"] = {"type": "object"}
    workload = tmp_path / "workload.jsonl"
    workload.write_text(json.dumps(data) + "\n")
    result = run_baseline(
        BaselineRunConfig(
            binary_path=ROOT / "tests/fixtures/bin/fake-llama-server",
            model_path=ROOT / "tests/fixtures/models/fake-model.gguf",
            workload_path=workload,
            output_dir=tmp_path / "baseline",
            response_format_mode=ResponseFormatMode.JSON_SCHEMA,
            extra_environment={"FAKE_LLAMA_SCENARIO": "healthy"},
        )
    )
    assert result.exit_code == 0 and result.summary is not None
    assert result.summary.execution.response_format_type == "json_schema"
    assert result.summary.quality.task_attempt_success_rate == 1.0
    request = json.loads((result.output_dir / "request-metrics.jsonl").read_text())["request"]
    assert request["response_format_schema"] == {"type": "object"}


def test_historical_profile_canonical_content_is_preserved() -> None:
    # This archive predates response_format_mode. Sanitized public paths differ
    # from private run hashes, so compare canonical content instead of run hashes.
    with zipfile.ZipFile(
        ROOT / "docs/evidence/aarchtune-real-arm64-smoke-30119492016-1.zip"
    ) as archive:
        plan = json.loads(archive.read("optimization/plan/search-plan.json"))
    for candidate in plan["candidates"]:
        historical = candidate["runtime"]
        runtime = ProfileRuntime.model_validate_json(json.dumps(historical))
        assert runtime.model_dump(mode="json") == historical
        assert profile_hash(runtime) == stable_hash(historical)
        constrained = runtime.model_copy(
            update={"response_format_mode": ResponseFormatMode.JSON_SCHEMA}
        )
        assert profile_hash(constrained) != profile_hash(runtime)
