"""Orchestration errors."""

from aarchtune.errors import AArchTuneError


class OrchestrationError(AArchTuneError):
    """Base one-command workflow error."""


class ResumeError(OrchestrationError):
    """Existing evidence cannot be resumed safely."""


class StageError(OrchestrationError):
    """A required stage did not produce valid evidence."""
