"""Orchestration errors."""

from aarchtune.errors import AArchTuneError


class OrchestrationError(AArchTuneError):
    """Base one-command workflow error."""


class ResumeError(OrchestrationError):
    """Existing evidence cannot be resumed safely."""


class StageError(OrchestrationError):
    """A required stage did not produce valid evidence."""


class PreflightBlockedError(OrchestrationError):
    """Baseline diagnosis completed but runtime tuning should not proceed."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        super().__init__(f"Baseline preflight blocked optimization: {outcome}")
