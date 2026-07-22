from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from aarchtune.workload.schema import (
    MAX_RESPONSE_CHARACTERS,
    ResponseInput,
    ValidatorDefinition,
    ValidatorResult,
)
from aarchtune.workload.validators import evaluate_validator, parse_response_json

DEFINITION_ADAPTER = TypeAdapter(ValidatorDefinition)


def _definition(value: dict[str, Any]) -> ValidatorDefinition:
    return DEFINITION_ADAPTER.validate_json(json.dumps(value))


def _response(
    text: str,
    *,
    request_succeeded: bool = True,
    timed_out: bool = False,
    status_code: int | None = 200,
    error: str | None = None,
) -> ResponseInput:
    return ResponseInput(
        task_id="task",
        text=text,
        request_succeeded=request_succeeded,
        timed_out=timed_out,
        status_code=status_code,
        error=error,
    )


def _evaluate(definition: dict[str, Any], response: ResponseInput) -> ValidatorResult:
    parsed = parse_response_json(response.text)
    return evaluate_validator(_definition(definition), response, parsed)


@pytest.mark.parametrize(
    ("text", "passed"),
    [
        ('{"ok":true}', True),
        ('```json\n{"ok":true}\n```', False),
        ('Explanation: {"ok":true}', False),
    ],
)
def test_valid_json_requires_the_entire_response(text: str, passed: bool) -> None:
    result = _evaluate({"type": "valid_json"}, _response(text))

    assert result.passed is passed


def test_json_schema_passes_and_reports_mismatch() -> None:
    definition = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"const": "ok"}},
        },
    }

    passing = _evaluate(definition, _response('{"status":"ok"}'))
    failing = _evaluate(definition, _response('{"status":"bad"}'))

    assert passing.passed is True
    assert failing.passed is False
    assert failing.path == "$.status"
    assert "Schema mismatch" in failing.reason


def test_required_fields_counts_null_as_present_and_missing_as_failure() -> None:
    definition = {"type": "required_fields", "paths": ["$.present", "$.other"]}

    passing = _evaluate(definition, _response('{"present":null,"other":1}'))
    failing = _evaluate(definition, _response('{"present":null}'))

    assert passing.passed is True
    assert failing.passed is False
    assert failing.path == "$.other"


def test_exact_value_does_not_coerce_boolean_to_number() -> None:
    definition = {"type": "exact_value", "path": "$.value", "expected": 1}

    passing = _evaluate(definition, _response('{"value":1}'))
    failing = _evaluate(definition, _response('{"value":true}'))

    assert passing.passed is True
    assert failing.passed is False
    assert failing.observed is True


def test_exact_and_allowed_value_report_missing_paths() -> None:
    exact = _evaluate(
        {"type": "exact_value", "path": "$.missing", "expected": "x"},
        _response("{}"),
    )
    allowed = _evaluate(
        {"type": "allowed_value", "path": "$.missing", "allowed": ["x"]},
        _response("{}"),
    )

    assert exact.passed is False
    assert allowed.passed is False
    assert "missing" in exact.reason


def test_allowed_value_uses_exact_json_equality() -> None:
    definition = {"type": "allowed_value", "path": "$.value", "allowed": [1, "yes"]}

    assert _evaluate(definition, _response('{"value":"yes"}')).passed is True
    assert _evaluate(definition, _response('{"value":true}')).passed is False


@pytest.mark.parametrize(
    ("definition", "text", "passed"),
    [
        ({"type": "contains_text", "text": "Ready"}, "Ready now", True),
        ({"type": "contains_text", "text": "Ready"}, "ready now", False),
        (
            {"type": "contains_text", "text": "Ready", "case_sensitive": False},
            "ready now",
            True,
        ),
        ({"type": "not_contains_text", "text": "delete"}, "safe retry", True),
        ({"type": "not_contains_text", "text": "delete"}, "delete state", False),
        (
            {"type": "not_contains_text", "text": "DELETE", "case_sensitive": False},
            "delete state",
            False,
        ),
    ],
)
def test_text_validators(definition: dict[str, Any], text: str, passed: bool) -> None:
    assert _evaluate(definition, _response(text)).passed is passed


def test_regex_match_supports_allowlisted_flags() -> None:
    definition = {
        "type": "regex_match",
        "pattern": "^status: ok$",
        "flags": ["IGNORECASE", "MULTILINE"],
    }

    assert _evaluate(definition, _response("header\nSTATUS: OK\nfooter")).passed is True
    assert _evaluate(definition, _response("status: failed")).passed is False


def test_maximum_response_length_includes_boundary() -> None:
    definition = {"type": "maximum_response_length", "max_characters": 4}

    boundary = _evaluate(definition, _response("éabc"))
    oversized = _evaluate(definition, _response("éabcd"))

    assert boundary.passed is True
    assert boundary.observed == 4
    assert oversized.passed is False


def test_request_succeeded_uses_timeout_and_failure_metadata() -> None:
    definition = {"type": "request_succeeded", "allow_timeout": False}

    passing = _evaluate(definition, _response("ok"))
    timeout = _evaluate(
        definition,
        _response("", request_succeeded=False, timed_out=True, status_code=None),
    )
    failure = _evaluate(
        definition,
        _response("", request_succeeded=False, status_code=503, error="unavailable"),
    )

    assert passing.passed is True
    assert timeout.passed is False
    assert "timed out" in timeout.reason
    assert failure.passed is False
    assert isinstance(failure.observed, dict)
    assert failure.observed["status_code"] == 503


def test_request_succeeded_can_explicitly_allow_timeout_metadata() -> None:
    result = _evaluate(
        {"type": "request_succeeded", "allow_timeout": True},
        _response("ok", request_succeeded=True, timed_out=True),
    )

    assert result.passed is True


def test_json_dependent_validator_still_returns_result_for_invalid_json() -> None:
    result = _evaluate(
        {"type": "exact_value", "path": "$.value", "expected": 1},
        _response("not json"),
    )

    assert result.passed is False
    assert "cannot resolve" in result.reason


def test_response_model_enforces_global_length_cap() -> None:
    with pytest.raises(ValidationError):
        _response("x" * (MAX_RESPONSE_CHARACTERS + 1))
