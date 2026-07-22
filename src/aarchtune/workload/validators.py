"""Execution of the ten supported declarative validators."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import cast

from jsonschema import Draft202012Validator

from aarchtune.workload.json_path import resolve_json_path
from aarchtune.workload.schema import (
    AllowedValueDefinition,
    ContainsTextDefinition,
    ExactValueDefinition,
    JsonSchemaDefinition,
    JsonValue,
    MaximumResponseLengthDefinition,
    NotContainsTextDefinition,
    RegexMatchDefinition,
    RequestSucceededDefinition,
    RequiredFieldsDefinition,
    ResponseInput,
    ValidatorDefinition,
    ValidatorResult,
    ValidatorType,
    ValidJsonDefinition,
    regex_flags_value,
)

MAX_OBSERVED_CHARACTERS = 4_096


@dataclass(frozen=True)
class ParsedJson:
    """One cached full-response JSON parse shared by dependent validators."""

    valid: bool
    value: object | None
    error: str | None


def parse_response_json(text: str) -> ParsedJson:
    try:
        return ParsedJson(valid=True, value=json.loads(text), error=None)
    except json.JSONDecodeError as exc:
        return ParsedJson(
            valid=False,
            value=None,
            error=f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
        )


def _safe_json(value: object) -> JsonValue:
    """Keep small JSON observations; summarize large values instead of duplicating bodies."""

    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return f"<non-JSON value of type {type(value).__name__}>"
    if len(serialized) > MAX_OBSERVED_CHARACTERS:
        return f"<observed {type(value).__name__} omitted: {len(serialized)} characters>"
    return cast(JsonValue, value)


def _json_equal(observed: object, expected: object) -> bool:
    """Compare JSON values without bool/number or string/number coercion."""

    return type(observed) is type(expected) and observed == expected


def _json_required_failure(definition_type: ValidatorType, parsed: ParsedJson) -> ValidatorResult:
    return ValidatorResult(
        passed=False,
        validator=definition_type,
        reason=f"Response is not valid JSON; validator cannot resolve JSON values: {parsed.error}",
    )


def evaluate_validator(
    definition: ValidatorDefinition,
    response: ResponseInput,
    parsed: ParsedJson,
) -> ValidatorResult:
    """Evaluate one schema-validated definition without executing workload content."""

    if isinstance(definition, ValidJsonDefinition):
        return ValidatorResult(
            passed=parsed.valid,
            validator=definition.type,
            reason="Response is valid JSON" if parsed.valid else parsed.error or "Invalid JSON",
        )

    if isinstance(definition, JsonSchemaDefinition):
        if not parsed.valid:
            return _json_required_failure(definition.type, parsed)
        errors = sorted(
            Draft202012Validator(definition.schema_definition).iter_errors(parsed.value),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if not errors:
            return ValidatorResult(
                passed=True,
                validator=definition.type,
                reason="Response matches the Draft 2020-12 JSON Schema",
            )
        error = errors[0]
        path = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}" for item in error.absolute_path
        )
        return ValidatorResult(
            passed=False,
            validator=definition.type,
            path=path,
            reason=f"JSON Schema mismatch: {error.message}",
            observed=_safe_json(error.instance),
        )

    if isinstance(definition, RequiredFieldsDefinition):
        if not parsed.valid:
            return _json_required_failure(definition.type, parsed)
        missing = [
            path for path in definition.paths if not resolve_json_path(parsed.value, path).found
        ]
        return ValidatorResult(
            passed=not missing,
            validator=definition.type,
            path=missing[0] if missing else None,
            reason=(
                "All required paths are present" if not missing else "Required paths are missing"
            ),
            observed=cast(JsonValue, missing),
            expected=cast(JsonValue, definition.paths),
        )

    if isinstance(definition, ExactValueDefinition):
        if not parsed.valid:
            return _json_required_failure(definition.type, parsed)
        resolution = resolve_json_path(parsed.value, definition.path)
        if not resolution.found:
            return ValidatorResult(
                passed=False,
                validator=definition.type,
                path=definition.path,
                reason=resolution.reason or "JSON path is missing",
                expected=definition.expected,
            )
        passed = _json_equal(resolution.value, definition.expected)
        return ValidatorResult(
            passed=passed,
            validator=definition.type,
            path=definition.path,
            reason="Observed value matches exactly" if passed else "Observed value does not match",
            observed=_safe_json(resolution.value),
            expected=definition.expected,
        )

    if isinstance(definition, AllowedValueDefinition):
        if not parsed.valid:
            return _json_required_failure(definition.type, parsed)
        resolution = resolve_json_path(parsed.value, definition.path)
        if not resolution.found:
            return ValidatorResult(
                passed=False,
                validator=definition.type,
                path=definition.path,
                reason=resolution.reason or "JSON path is missing",
                expected=definition.allowed,
            )
        passed = any(_json_equal(resolution.value, allowed) for allowed in definition.allowed)
        return ValidatorResult(
            passed=passed,
            validator=definition.type,
            path=definition.path,
            reason=(
                "Observed value is in the allowed set"
                if passed
                else "Observed value is not in the allowed set"
            ),
            observed=_safe_json(resolution.value),
            expected=definition.allowed,
        )

    if isinstance(definition, (ContainsTextDefinition, NotContainsTextDefinition)):
        haystack = response.text if definition.case_sensitive else response.text.casefold()
        needle = definition.text if definition.case_sensitive else definition.text.casefold()
        contains = needle in haystack
        passed = contains if isinstance(definition, ContainsTextDefinition) else not contains
        if isinstance(definition, ContainsTextDefinition):
            reason = "Required text is present" if passed else "Required text is absent"
        else:
            reason = "Forbidden text is absent" if passed else "Forbidden text is present"
        return ValidatorResult(
            passed=passed,
            validator=definition.type,
            reason=reason,
            observed=contains,
            expected=definition.text,
        )

    if isinstance(definition, RegexMatchDefinition):
        matched = re.search(
            definition.pattern,
            response.text,
            flags=regex_flags_value(definition.flags),
        )
        return ValidatorResult(
            passed=matched is not None,
            validator=definition.type,
            reason="Regular expression matched" if matched else "Regular expression did not match",
            observed=matched.group(0) if matched else None,
            expected=definition.pattern,
        )

    if isinstance(definition, MaximumResponseLengthDefinition):
        observed_length = len(response.text)
        passed = observed_length <= definition.max_characters
        return ValidatorResult(
            passed=passed,
            validator=definition.type,
            reason=(
                "Response length is within the limit"
                if passed
                else "Response length exceeds the limit"
            ),
            observed=observed_length,
            expected=definition.max_characters,
        )

    if isinstance(definition, RequestSucceededDefinition):
        timeout_allowed = definition.allow_timeout or not response.timed_out
        passed = response.request_succeeded and timeout_allowed
        if response.timed_out and not definition.allow_timeout:
            reason = "Request timed out and timeouts are not allowed"
        elif not response.request_succeeded:
            reason = "Request did not complete successfully"
        else:
            reason = "Request completed successfully"
        return ValidatorResult(
            passed=passed,
            validator=definition.type,
            reason=reason,
            observed={
                "request_succeeded": response.request_succeeded,
                "timed_out": response.timed_out,
                "status_code": response.status_code,
                "error": response.error,
            },
            expected={"request_succeeded": True, "allow_timeout": definition.allow_timeout},
        )

    raise AssertionError(f"Unsupported validated definition: {type(definition).__name__}")
