"""Expected workload loading and evaluation failures."""

from __future__ import annotations

from aarchtune.errors import AArchTuneError


class WorkloadError(AArchTuneError):
    """Base class for workload subsystem errors."""


class WorkloadLoadError(WorkloadError):
    """A workload file could not be loaded or validated."""

    def __init__(
        self,
        message: str,
        *,
        line_number: int | None = None,
        task_id: str | None = None,
    ) -> None:
        self.message = message
        self.line_number = line_number
        self.task_id = task_id
        context: list[str] = []
        if line_number is not None:
            context.append(f"line {line_number}")
        if task_id is not None:
            context.append(f"task {task_id!r}")
        prefix = f" ({', '.join(context)})" if context else ""
        super().__init__(f"{message}{prefix}")


class ResponseFixtureError(WorkloadError):
    """A response fixture file is malformed or inconsistent with a workload."""

    def __init__(self, message: str, *, line_number: int | None = None) -> None:
        self.message = message
        self.line_number = line_number
        suffix = f" (line {line_number})" if line_number is not None else ""
        super().__init__(f"{message}{suffix}")


class JsonPathError(WorkloadError):
    """A restricted JSON path is malformed."""


class EvaluationInputError(WorkloadError):
    """Response inputs cannot be matched safely to the workload."""
