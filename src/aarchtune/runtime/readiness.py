"""Bounded network readiness polling independent of process ownership."""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from aarchtune.runtime.client import LlamaServerClient
from aarchtune.runtime.errors import ReadinessTimeoutError, ServerExitedError


class ReadinessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ready: bool
    method: str
    endpoint: str
    attempts: int
    elapsed_seconds: float


def wait_for_readiness(
    client: LlamaServerClient,
    *,
    process_poll: Callable[[], int | None],
    log_tail: Callable[[], str],
    endpoints: tuple[str, ...],
    startup_timeout_seconds: float,
    poll_interval_seconds: float = 0.1,
    per_probe_timeout_seconds: float = 0.5,
) -> ReadinessResult:
    """Require a successful configured HTTP endpoint before declaring readiness."""

    started = time.monotonic()
    deadline = started + startup_timeout_seconds
    attempts = 0
    last_error: str | None = None
    while True:
        return_code = process_poll()
        if return_code is not None:
            raise ServerExitedError(return_code, log_tail())
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            raise ReadinessTimeoutError(startup_timeout_seconds, last_error, log_tail())
        for endpoint in endpoints:
            return_code = process_poll()
            if return_code is not None:
                raise ServerExitedError(return_code, log_tail())
            attempts += 1
            probe_timeout = max(0.01, min(per_probe_timeout_seconds, deadline - time.monotonic()))
            result = client.get_readiness(endpoint, timeout_seconds=probe_timeout)
            if result.succeeded:
                elapsed = time.monotonic() - started
                method = "health_endpoint" if endpoint == "/health" else "http_endpoint"
                return ReadinessResult(
                    ready=True,
                    method=method,
                    endpoint=f"{client.base_url}{endpoint}",
                    attempts=attempts,
                    elapsed_seconds=elapsed,
                )
            last_error = result.error
            if time.monotonic() >= deadline:
                break
        sleep_seconds = min(poll_interval_seconds, max(0.0, deadline - time.monotonic()))
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
