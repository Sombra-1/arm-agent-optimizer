"""Bounded non-streaming HTTP client for one local llama-server."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, cast

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue

from aarchtune.workload.schema import ResponseInput, WorkloadTask


class ClientFailureKind(StrEnum):
    CONNECTION_FAILURE = "connection_failure"
    REQUEST_TIMEOUT = "request_timeout"
    HTTP_ERROR = "http_error"
    SERVER_ERROR = "server_error"
    INVALID_JSON = "invalid_json"
    MISSING_CONTENT = "missing_completion_content"
    RESPONSE_TOO_LARGE = "response_too_large"


class HttpResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    succeeded: bool
    status_code: int | None = None
    error_kind: ClientFailureKind | None = None
    error: str | None = None
    text: str | None = None
    json_data: JsonValue = None


class ChatCompletionResult(BaseModel):
    """Workload-compatible response plus optional raw server JSON for metrics."""

    model_config = ConfigDict(extra="forbid", strict=True)

    response: ResponseInput
    raw_json: JsonValue = None


class LlamaServerClient:
    """HTTPX wrapper that never interprets model content as executable data."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        request_timeout_seconds: float = 60.0,
        maximum_response_characters: int = 1024 * 1024,
    ) -> None:
        bracketed_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        self.base_url = f"http://{bracketed_host}:{port}"
        self.request_timeout_seconds = request_timeout_seconds
        self.maximum_response_characters = maximum_response_characters
        self._client = httpx.Client(base_url=self.base_url, trust_env=False)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LlamaServerClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        timeout_seconds: float | None = None,
        json_body: dict[str, Any] | None = None,
        require_json: bool = False,
    ) -> HttpResult:
        timeout = timeout_seconds or self.request_timeout_seconds
        try:
            response = self._client.request(
                method,
                endpoint,
                json=json_body,
                timeout=httpx.Timeout(timeout),
            )
        except httpx.TimeoutException:
            return HttpResult(
                succeeded=False,
                error_kind=ClientFailureKind.REQUEST_TIMEOUT,
                error=f"request_timeout: request exceeded {timeout:.2f} seconds",
            )
        except httpx.ConnectError as exc:
            return HttpResult(
                succeeded=False,
                error_kind=ClientFailureKind.CONNECTION_FAILURE,
                error=f"connection_failure: {exc}",
            )
        except httpx.RequestError as exc:
            return HttpResult(
                succeeded=False,
                error_kind=ClientFailureKind.CONNECTION_FAILURE,
                error=f"connection_failure: {exc}",
            )

        status_code = response.status_code
        raw_content = response.content
        if len(raw_content) > self.maximum_response_characters * 4:
            return HttpResult(
                succeeded=False,
                status_code=status_code,
                error_kind=ClientFailureKind.RESPONSE_TOO_LARGE,
                error=(
                    "response_too_large: HTTP body exceeded the configured decoded response limit"
                ),
            )
        text = response.text
        if len(text) > self.maximum_response_characters:
            return HttpResult(
                succeeded=False,
                status_code=status_code,
                error_kind=ClientFailureKind.RESPONSE_TOO_LARGE,
                error=(
                    f"response_too_large: response contained {len(text)} characters; "
                    f"maximum is {self.maximum_response_characters}"
                ),
            )
        if not response.is_success:
            kind = (
                ClientFailureKind.SERVER_ERROR
                if status_code >= 500
                else ClientFailureKind.HTTP_ERROR
            )
            return HttpResult(
                succeeded=False,
                status_code=status_code,
                error_kind=kind,
                error=f"{kind.value}: HTTP {status_code}: {text[:500]}",
            )

        json_data: JsonValue = None
        if require_json:
            try:
                json_data = cast(JsonValue, response.json())
            except ValueError as exc:
                return HttpResult(
                    succeeded=False,
                    status_code=status_code,
                    error_kind=ClientFailureKind.INVALID_JSON,
                    error=f"invalid_json: HTTP response was not valid JSON: {exc}",
                )
        return HttpResult(
            succeeded=True,
            status_code=status_code,
            text=text,
            json_data=json_data,
        )

    def get_readiness(self, endpoint: str, *, timeout_seconds: float) -> HttpResult:
        return self._request("GET", endpoint, timeout_seconds=timeout_seconds)

    def get_models(self) -> HttpResult:
        return self._request("GET", "/v1/models", require_json=True)

    def get_metrics(self) -> HttpResult:
        return self._request("GET", "/metrics")

    def chat_completion_detailed(self, task: WorkloadTask) -> ChatCompletionResult:
        payload: dict[str, Any] = {
            "messages": [message.model_dump(mode="json") for message in task.messages],
            "temperature": task.generation.temperature,
            "max_tokens": task.generation.max_tokens,
            "seed": task.generation.seed,
            "stream": False,
        }
        result = self._request(
            "POST",
            "/v1/chat/completions",
            json_body=payload,
            require_json=True,
        )
        if not result.succeeded:
            return ChatCompletionResult(
                response=ResponseInput(
                    task_id=task.id,
                    text="",
                    request_succeeded=False,
                    timed_out=result.error_kind is ClientFailureKind.REQUEST_TIMEOUT,
                    status_code=result.status_code,
                    error=result.error,
                ),
                raw_json=result.json_data,
            )

        data = result.json_data
        content: object | None = None
        if isinstance(data, dict):
            choices = data.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict):
                    content = message.get("content")
        if not isinstance(content, str):
            return ChatCompletionResult(
                response=ResponseInput(
                    task_id=task.id,
                    text="",
                    request_succeeded=False,
                    timed_out=False,
                    status_code=result.status_code,
                    error=(
                        f"{ClientFailureKind.MISSING_CONTENT.value}: "
                        "response did not contain choices[0].message.content"
                    ),
                ),
                raw_json=result.json_data,
            )
        if len(content) > self.maximum_response_characters:
            return ChatCompletionResult(
                response=ResponseInput(
                    task_id=task.id,
                    text="",
                    request_succeeded=False,
                    timed_out=False,
                    status_code=result.status_code,
                    error=(
                        f"{ClientFailureKind.RESPONSE_TOO_LARGE.value}: completion contained "
                        f"{len(content)} characters"
                    ),
                ),
                raw_json=result.json_data,
            )
        return ChatCompletionResult(
            response=ResponseInput(
                task_id=task.id,
                text=content,
                request_succeeded=True,
                timed_out=False,
                status_code=result.status_code,
                error=None,
            ),
            raw_json=result.json_data,
        )

    def chat_completion(self, task: WorkloadTask) -> ResponseInput:
        return self.chat_completion_detailed(task).response


def execute_workload_task(client: LlamaServerClient, task: WorkloadTask) -> ResponseInput:
    """Narrow adapter preserving task ID and generation settings without interpretation."""

    return client.chat_completion(task)
