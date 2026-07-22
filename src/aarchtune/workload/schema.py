"""Strict, persisted schemas for workloads and quality evaluation."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic import (
    JsonValue as PydanticJsonValue,
)

from aarchtune.workload.json_path import parse_json_path

# Defensive v1 limits. They apply before later runtime components see workload data.
MAX_WORKLOAD_FILE_BYTES = 5 * 1024 * 1024
MAX_TASKS = 1_000
MAX_JSONL_LINE_BYTES = 256 * 1024
MAX_MESSAGES_PER_TASK = 32
MAX_MESSAGE_CONTENT_CHARACTERS = 64 * 1024
MAX_VALIDATORS_PER_TASK = 32
MAX_REGEX_CHARACTERS = 1_024
MAX_RESPONSE_CHARACTERS = 1024 * 1024
MAX_GENERATION_TOKENS = 32_768

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1)]
JsonValue: TypeAlias = PydanticJsonValue


class WorkloadModel(BaseModel):
    """Strict base for all untrusted JSONL records."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ChatMessage(WorkloadModel):
    """One deterministic chat-completion input message."""

    role: Literal["system", "user", "assistant"]
    content: Annotated[
        str,
        StringConstraints(min_length=1, max_length=MAX_MESSAGE_CONTENT_CHARACTERS),
    ]

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        return value


class GenerationSettings(WorkloadModel):
    """Generation controls held constant across future candidate profiles."""

    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0
    max_tokens: Annotated[int, Field(ge=1, le=MAX_GENERATION_TOKENS)] = 200
    seed: int | None = 42


class ValidatorType(StrEnum):
    VALID_JSON = "valid_json"
    JSON_SCHEMA = "json_schema"
    REQUIRED_FIELDS = "required_fields"
    EXACT_VALUE = "exact_value"
    ALLOWED_VALUE = "allowed_value"
    CONTAINS_TEXT = "contains_text"
    NOT_CONTAINS_TEXT = "not_contains_text"
    REGEX_MATCH = "regex_match"
    MAXIMUM_RESPONSE_LENGTH = "maximum_response_length"
    REQUEST_SUCCEEDED = "request_succeeded"


class RegexFlag(StrEnum):
    IGNORECASE = "IGNORECASE"
    MULTILINE = "MULTILINE"
    DOTALL = "DOTALL"
    ASCII = "ASCII"


REGEX_FLAG_VALUES: dict[RegexFlag, re.RegexFlag] = {
    RegexFlag.IGNORECASE: re.IGNORECASE,
    RegexFlag.MULTILINE: re.MULTILINE,
    RegexFlag.DOTALL: re.DOTALL,
    RegexFlag.ASCII: re.ASCII,
}


def regex_flags_value(flags: list[RegexFlag]) -> re.RegexFlag:
    combined = re.RegexFlag(0)
    for flag in flags:
        combined |= REGEX_FLAG_VALUES[flag]
    return combined


class ValidJsonDefinition(WorkloadModel):
    type: Literal[ValidatorType.VALID_JSON]


class JsonSchemaDefinition(WorkloadModel):
    type: Literal[ValidatorType.JSON_SCHEMA]
    schema_definition: dict[str, JsonValue] = Field(alias="schema", serialization_alias="schema")

    @model_validator(mode="after")
    def validate_schema_definition(self) -> JsonSchemaDefinition:
        try:
            Draft202012Validator.check_schema(self.schema_definition)
        except SchemaError as exc:
            raise ValueError(f"invalid Draft 2020-12 JSON Schema: {exc.message}") from exc
        return self


class RequiredFieldsDefinition(WorkloadModel):
    type: Literal[ValidatorType.REQUIRED_FIELDS]
    paths: Annotated[list[str], Field(min_length=1, max_length=MAX_VALIDATORS_PER_TASK)]

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, paths: list[str]) -> list[str]:
        for path in paths:
            parse_json_path(path)
        return paths


class ExactValueDefinition(WorkloadModel):
    type: Literal[ValidatorType.EXACT_VALUE]
    path: str
    expected: JsonValue

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        parse_json_path(path)
        return path


class AllowedValueDefinition(WorkloadModel):
    type: Literal[ValidatorType.ALLOWED_VALUE]
    path: str
    allowed: Annotated[list[JsonValue], Field(min_length=1)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        parse_json_path(path)
        return path


class ContainsTextDefinition(WorkloadModel):
    type: Literal[ValidatorType.CONTAINS_TEXT]
    text: NonEmptyString
    case_sensitive: bool = True


class NotContainsTextDefinition(WorkloadModel):
    type: Literal[ValidatorType.NOT_CONTAINS_TEXT]
    text: NonEmptyString
    case_sensitive: bool = True


class RegexMatchDefinition(WorkloadModel):
    type: Literal[ValidatorType.REGEX_MATCH]
    pattern: Annotated[str, StringConstraints(min_length=1, max_length=MAX_REGEX_CHARACTERS)]
    flags: list[RegexFlag] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_pattern(self) -> RegexMatchDefinition:
        if len(set(self.flags)) != len(self.flags):
            raise ValueError("regex flags must not be repeated")
        try:
            re.compile(self.pattern, regex_flags_value(self.flags))
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return self


class MaximumResponseLengthDefinition(WorkloadModel):
    type: Literal[ValidatorType.MAXIMUM_RESPONSE_LENGTH]
    max_characters: Annotated[int, Field(ge=0, le=MAX_RESPONSE_CHARACTERS)]


class RequestSucceededDefinition(WorkloadModel):
    type: Literal[ValidatorType.REQUEST_SUCCEEDED]
    allow_timeout: bool = False


ValidatorDefinition = Annotated[
    ValidJsonDefinition
    | JsonSchemaDefinition
    | RequiredFieldsDefinition
    | ExactValueDefinition
    | AllowedValueDefinition
    | ContainsTextDefinition
    | NotContainsTextDefinition
    | RegexMatchDefinition
    | MaximumResponseLengthDefinition
    | RequestSucceededDefinition,
    Field(discriminator="type"),
]


class WorkloadTask(WorkloadModel):
    """One ordered workload task and all declarative quality constraints."""

    id: NonEmptyString
    category: NonEmptyString
    description: NonEmptyString
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=MAX_MESSAGES_PER_TASK)]
    generation: GenerationSettings
    validators: Annotated[
        list[ValidatorDefinition], Field(min_length=1, max_length=MAX_VALIDATORS_PER_TASK)
    ]

    @field_validator("id", "category", "description")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class LoadedWorkload(WorkloadModel):
    """Validated workload plus exact-byte provenance."""

    path: Path
    sha256: str
    byte_size: int
    tasks: list[WorkloadTask]

    @property
    def deterministic(self) -> bool:
        return all(
            task.generation.temperature == 0 and task.generation.seed is not None
            for task in self.tasks
        )


class ResponseInput(WorkloadModel):
    """Model-like response and request metadata supplied by fixtures or future runners."""

    task_id: NonEmptyString
    text: Annotated[str, StringConstraints(max_length=MAX_RESPONSE_CHARACTERS)]
    request_succeeded: bool
    timed_out: bool
    status_code: Annotated[int, Field(ge=100, le=599)] | None = None
    error: str | None = None


class ValidatorResult(WorkloadModel):
    """Detailed outcome for one declared validator."""

    passed: bool
    validator: ValidatorType
    path: str | None = None
    reason: str
    observed: JsonValue = None
    expected: JsonValue = None


class TaskEvaluationResult(WorkloadModel):
    """Ordered validator outcomes for one workload task."""

    task_id: str
    category: str
    evaluated: bool
    passed: bool | None
    reason: str | None = None
    validator_results: list[ValidatorResult] = Field(default_factory=list)


class CategoryStatistics(WorkloadModel):
    total_tasks: int
    tasks_evaluated: int
    tasks_missing_responses: int
    task_pass_count: int
    task_failure_count: int
    task_success_rate: float | None


class ValidatorTypeStatistics(WorkloadModel):
    total: int
    passed: int
    failed: int
    pass_rate: float | None


class WorkloadEvaluationSummary(WorkloadModel):
    """Full-precision aggregate quality metrics for one fixture evaluation."""

    total_tasks: int
    tasks_evaluated: int
    tasks_missing_responses: int
    task_pass_count: int
    task_failure_count: int
    task_success_rate: float | None
    total_validators: int
    validator_pass_count: int
    validator_failure_count: int
    validator_pass_rate: float | None
    json_valid_response_count: int
    json_validity_rate: float | None
    request_success_count: int
    request_success_rate: float | None
    timeout_count: int
    timeout_rate: float | None
    per_category: dict[str, CategoryStatistics]
    per_validator_type: dict[ValidatorType, ValidatorTypeStatistics]
    unavailable_reasons: dict[str, str] = Field(default_factory=dict)
    task_results: list[TaskEvaluationResult]


class WorkloadValidationSummary(WorkloadModel):
    path: Path
    sha256: str
    byte_size: int
    tasks: int
    categories: int
    category_counts: dict[str, int]
    validators: int
    validator_counts: dict[ValidatorType, int]
    deterministic: bool
